#!/usr/bin/env python3
"""
网络搜索核心实现（博查 API + 网页正文抓取）

鲁棒性设计：
- Key 别名：BOCHA_API_KEY / BochaKey；无 Key 时直接返回“不可用”结果，不抛异常（上层降级为 LLM 自身知识）
- API 调用：超时 + 指数退避重试（仅 5xx/429/网络错误重试）+ 熔断（连续失败后一段时间直接跳过）
- 正文抓取：线程池并行 + 单页超时 + 整体时间预算，超时的页面退回 snippet/summary
- 结果清洗：去重（URL）、截断、过滤空内容；内容视为不可信数据（仅作参考资料注入 prompt）
- 取消：每一步检查 RunContext 取消令牌
- 缓存：进程内 LRU（同一 query 短时间内重复检索直接复用，减少多轮 WebSearch 成本）
"""
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from typing import Any, Dict, List, Optional

import requests

from ..runtime.breaker import get_breaker
from ..runtime.context import current

API_URL = "https://api.bocha.cn/v1/web-search"
KEY_ALIASES = ("BOCHA_API_KEY", "BochaKey", "BOCHA_KEY")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")

_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 600

# 测试注入：fn(query, num_results) -> list[dict]
_mock_search = None


def set_mock_search(fn):
    global _mock_search
    _mock_search = fn


def _api_key() -> str:
    for k in KEY_ALIASES:
        if os.environ.get(k):
            return os.environ[k]
    return ""


def _cancelled() -> bool:
    ctx = current()
    return bool(ctx and ctx.cancel.cancelled)


def extract_legal_keywords(query: str) -> List[str]:
    stopwords = {'的', '了', '和', '是', '在', '有', '与', '或', '等', '及', '为', '以', '对', '将', '把', '被', '请',
                 '如', '若', '以及', '及其', '且', '并', '但', '或者', '如果', '当', '时', '应当', '可以', '必须', '不得',
                 '这个', '那个', '什么', '如何', '怎么', '为什么', '因为', '所以', '但是', '而且'}
    words = re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]+', query)
    keywords = [w for w in words if len(w) >= 2 and w not in stopwords]
    legal = [kw for kw in keywords if any(ch in kw for ch in '合同协议条款义务权利责任违约金赔偿损失变更解除终止履行')]
    if not legal and len(keywords) >= 2:
        legal = keywords[:3]
    return legal


def format_legal_query(query: str) -> str:
    # 已是结构化检索词（含“范本/条款”）时原样使用，避免关键词抽取破坏语义
    if any(k in (query or "") for k in ("范本", "条款", "示范文本")):
        return " ".join(str(query).split())
    kws = extract_legal_keywords(query)
    if kws:
        q = ' '.join(kws)
        return q if '合同' in q or '协议' in q or '条款' in q else q + ' 合同条款'
    return (query or '').strip() + ' 合同条款'


def fetch_page(url: str, timeout: float = 8.0, max_chars: int = 6000) -> str:
    from bs4 import BeautifulSoup
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=(4, timeout), stream=True)
    resp.raise_for_status()
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        return ""
    raw = resp.raw.read(2_000_000, decode_content=True)
    resp.encoding = resp.apparent_encoding
    html = raw.decode(resp.encoding or "utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
        tag.decompose()
    lines = [ln.strip() for ln in soup.get_text(separator="\n").splitlines() if len(ln.strip()) > 5]
    text = "\n".join(lines)
    return text[:max_chars] + ("……（内容已截断）" if len(text) > max_chars else "")


def call_api(query: str, num_results: int = 8, retries: int = 2, timeout: float = 15.0) -> Dict[str, Any]:
    key = _api_key()
    if not key:
        return {"error": "未配置博查 API Key（BOCHA_API_KEY / BochaKey）"}
    breaker = get_breaker("bocha", failure_threshold=3, recovery_timeout=60)
    if not breaker.allow():
        return {"error": "Web 搜索服务熔断中，暂时跳过"}
    last = ""
    for attempt in range(retries + 1):
        if _cancelled():
            return {"error": "cancelled"}
        try:
            r = requests.post(API_URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              json={"query": query, "count": num_results, "summary": True}, timeout=(5, timeout))
            if r.status_code == 200:
                breaker.record_success()
                return r.json()
            last = f"HTTP {r.status_code}"
            if r.status_code not in (429, 500, 502, 503, 504):
                return {"error": f"API调用失败: {last}"}
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        breaker.record_failure()
        time.sleep(min(4.0, 0.8 * (2 ** attempt)))
    return {"error": f"API调用失败（重试后）: {last}"}


_H = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9", "Accept": "text/html,application/xhtml+xml"}


def _html_search(name: str, url: str, params: dict, item_sel: str, title_sel: str, snippet_sel: str,
                 num_results: int, timeout: float = 10.0) -> List[dict]:
    """通用 HTML 结果页解析（无 Key 公开检索源）。每个源独立熔断"""
    from bs4 import BeautifulSoup
    breaker = get_breaker(f"search:{name}", failure_threshold=3, recovery_timeout=120)
    if not breaker.allow():
        return []
    try:
        r = requests.get(url, params=params, headers=_H, timeout=(5, timeout))
        r.raise_for_status()
    except requests.RequestException:
        breaker.record_failure()
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for it in soup.select(item_sel):
        a = it.select_one(title_sel)
        if not a:
            continue
        href = a.get("data-mdurl") or a.get("href") or ""
        if href.startswith("/"):
            href = requests.compat.urljoin(url, href)
        if not href.startswith("http"):
            continue
        p = it.select_one(snippet_sel) if snippet_sel else None
        out.append({"name": a.get_text(strip=True), "url": href, "snippet": p.get_text(" ", strip=True)[:300] if p else ""})
        if len(out) >= num_results:
            break
    if out:
        breaker.record_success()
    else:
        breaker.record_failure()
    return out


def so360_search(query: str, num_results: int = 8) -> List[dict]:
    return _html_search("360", "https://www.so.com/s", {"q": query}, "li.res-list", "h3 a", ".res-desc, p",
                        num_results)


def sogou_search(query: str, num_results: int = 8) -> List[dict]:
    return _html_search("sogou", "https://www.sogou.com/web", {"query": query}, "div.vrwrap, div.rb", "h3 a",
                        ".space-txt, .str_info, p", num_results)


def bing_search(query: str, num_results: int = 8, timeout: float = 10.0) -> List[dict]:
    """备用检索源：Bing 网页结果解析（无需 Key）。仅在主检索源不可用时使用"""
    from urllib.parse import quote_plus
    from bs4 import BeautifulSoup
    breaker = get_breaker("bing", failure_threshold=3, recovery_timeout=120)
    if not breaker.allow():
        return []
    try:
        r = requests.get(f"https://cn.bing.com/search?q={quote_plus(query)}&ensearch=0",
                         headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=(5, timeout))
        r.raise_for_status()
    except requests.RequestException:
        breaker.record_failure()
        return []
    breaker.record_success()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        p = li.select_one(".b_caption p") or li.select_one("p")
        if not a or not a.get("href", "").startswith("http"):
            continue
        out.append({"name": a.get_text(strip=True), "url": a["href"], "snippet": p.get_text(strip=True) if p else ""})
        if len(out) >= num_results:
            break
    return out


def _on_topic(query: str, items: List[dict]) -> bool:
    """至少一半结果标题包含“合同/协议/范本/条款”且不是百科/词典/机关门户页"""
    bad = ("百度百科", "汉典", "词典", "百度知道", "的意思", "拼音", "门户", "局网站")
    good = sum(1 for it in items if any(k in it.get("name", "") for k in ("合同", "协议", "范本", "条款", "模板"))
               and not any(b in it.get("name", "") for b in bad))
    return good * 2 >= max(1, len(items))


def _alternatives(query: str) -> List[str]:
    import re as _re
    q = query.replace(" ", "")
    m = _re.match(r"^(.*?)(合同|协议)", q)
    core = (m.group(1) + m.group(2)) if m else q
    alts = [f"{core}范本", f"{core}模板", f"{core}书范本"]
    if len(core) > 6:   # 去掉修饰前缀，如“数字内容授权协议”→“授权协议”
        alts += [f"{core[-4:]}范本", f"{core[-6:]}范本"]
    seen, out = {query}, []
    for a in alts:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _items(response: Dict[str, Any]) -> List[dict]:
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    pages = (data or {}).get("webPages", {}) or {}
    return pages.get("value", []) or []


def search(query: str, num_results: int = 8, fetch_content: bool = True, time_budget: float = 25.0,
           page_timeout: float = 8.0) -> List[Dict[str, Any]]:
    """返回 [{title,url,content,summary,source,ok}]；失败时返回带 error 的单条结果（不抛异常）"""
    formatted = format_legal_query(query)
    cache_key = f"{formatted}|{num_results}|{fetch_content}"
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return [dict(x) for x in hit[1]]

    if _mock_search is not None:
        results = _mock_search(formatted, num_results)
    else:
        # 检索源降级链：博查 API → 360 → 搜狗 → Bing → 空结果（上层改用模型自身知识）
        # 每个源结果都做主题校验（防止长尾词退化为首字匹配的百科页），不合格则尝试下一个源
        response = call_api(formatted, num_results)
        provider = "bocha"
        items = _items(response) if "error" not in response else []
        if not items:
            fallback_items, fallback_provider = [], ""
            for provider, fn in (("360", so360_search), ("sogou", sogou_search), ("bing", bing_search)):
                if _cancelled():
                    break
                got = fn(formatted, num_results)
                if got and _on_topic(formatted, got):
                    items = got
                    break
                if got and not fallback_items:
                    fallback_items, fallback_provider = got, provider
            if not items and fallback_items:
                items, provider = fallback_items, fallback_provider
        if not items:
            err = response.get("error", "无结果")
            return [{"title": "", "url": "", "content": "", "summary": f"网络搜索不可用：{err}",
                     "ok": False, "error": err}]
        seen, results = set(), []
        for it in items:
            url = it.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            results.append({"title": it.get("name", ""), "url": url, "summary": it.get("summary") or it.get("snippet", ""),
                            "content": it.get("snippet", ""), "ok": True, "provider": provider})
            if len(results) >= num_results:
                break
        if fetch_content and results:
            deadline = time.monotonic() + time_budget
            pool = ThreadPoolExecutor(max_workers=min(6, len(results)))
            futs = {pool.submit(fetch_page, r["url"], page_timeout): r for r in results}
            pending = set(futs)
            while pending and time.monotonic() < deadline and not _cancelled():
                done, pending = wait(pending, timeout=min(1.0, max(0.05, deadline - time.monotonic())),
                                     return_when=FIRST_COMPLETED)
                for f in done:
                    r = futs[f]
                    try:
                        text = f.result()
                        if text:
                            r["content"] = text
                    except Exception as e:  # noqa: BLE001
                        r["fetch_error"] = type(e).__name__
            for f in pending:
                futs[f]["fetch_error"] = "timeout"
                f.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
    results = [r for r in results if (r.get("content") or r.get("summary"))]
    ctx = current()
    if ctx is not None:  # 检索留痕：供评测（噪声率/利用率）与前端展示
        ctx.emit("retrieval.web", {"query": query, "formatted_query": formatted,
                                   "results": [{"title": r.get("title", ""), "url": r.get("url", ""),
                                                "summary": (r.get("summary") or "")[:300],
                                                "content": (r.get("content") or "")[:1200]} for r in results]})
    if not results:
        results = [{"title": "", "url": "", "content": "", "summary": "网络搜索暂无结果", "ok": False}]
    else:
        with _cache_lock:
            _cache[cache_key] = (time.time(), [dict(x) for x in results])
            if len(_cache) > 256:
                for k in sorted(_cache, key=lambda k: _cache[k][0])[:64]:
                    _cache.pop(k, None)
    return results


class WebSearchTool:
    """Agent 工具：web_search(query, num_results)"""

    def __init__(self, outline_manager=None):
        self.outline_manager = outline_manager

    def execute(self, query: str, num_results: int = 5) -> str:
        if not str(query or "").strip():
            return "错误：query 不能为空"
        num_results = max(1, min(int(num_results or 5), 8))
        results = search(query, num_results=num_results)
        slim = [{"title": r.get("title", ""), "url": r.get("url", ""), "summary": r.get("summary", ""),
                 "content": (r.get("content") or "")[:2500]} for r in results]
        return json.dumps(slim, ensure_ascii=False, indent=2)

    def get_search_results(self, query: str, num_results: int = 8) -> List[Dict[str, Any]]:
        return search(query, num_results=num_results)
