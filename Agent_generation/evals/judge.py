"""LLM-as-Judge 引擎 —— 可复现、可审计

协议（每条规则都对应一个已知的 Judge 失效模式）：
1. 分析型 Rubric：每个维度拆成二值检查项（yes/no/na），不让模型直接打 1~10 的整体分（整体分方差大、不可解释）
2. 证据绑定：yes 必须给出原文逐字引用 + locator；程序核验引用确实存在于被评文本中，
   无法核验的 yes 降级为 no 并记为 ungrounded（抑制幻觉式“通过”）
3. 多次采样 + 多数投票：k 次独立评审，每次打乱检查项顺序（抑制位置偏差），逐项多数决；报告 Fleiss κ
4. 成对比较双向交换：A/B 与 B/A 各评一次，两次结论一致才计胜负，否则计平局（抑制位置偏差）
5. 固定配置：Judge 模型 / 温度 / Rubric 版本（内容哈希）写入报告
6. 缓存 + 审计：每次调用以 (rubric版本, 模型, prompt 哈希, 采样序号) 为键缓存原始响应；
   重跑报告完全复现；审计日志保留 prompt 哈希、原始输出、解析结果、核验结论
"""
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .common import CACHE_DIR, grounded, normalize, sha

JUDGE_SYSTEM = """你是一名严谨的合同审查评测员（法律专业）。你的任务是按给定检查项，逐项判断【被评文本】是否满足要求。

判定规则（必须遵守）：
1. 只依据【被评文本】中实际写出的内容判断，不做善意推断，不脑补“应该有”。
2. verdict 取值：
   - "yes"：文本中有明确内容满足该检查项；
   - "no"：不满足、仅部分满足、表述含糊、或用占位符（如 {月租金金额}）代替了【用户需求】中已明确给出的具体数值；
   - 占位符口径：用户未提供的信息（当事人身份、日期、账户、用户未约定的费率/期限等）使用 {占位符} 是合同范本的正常做法，
     只要条款写明了该事项及其计算方式/确定规则，就不因占位符判 no；编造用户未提供的具体数值不额外加分；
   - "na"：检查项与该合同明显无关（极少使用）。
3. 判定为 yes 时，evidence 必须是【被评文本】中的逐字原文片段（10~80 字，可直接复制，不得改写、不得拼接多处），
   并给出该片段所在的 locator（文本中方括号内的编号，如 3.2；首部/尾部写 header/footer）。
   判定为 no 时，evidence 填最相关的原文片段或空字符串，reason 说明缺什么。
4. 每个检查项独立判断，不受其他检查项影响；不因文本长、措辞华丽而放宽标准。
5. 只输出 JSON，不要输出任何其他内容。

输出格式：
{"items": [{"id": "检查项ID", "verdict": "yes|no|na", "evidence": "原文片段", "locator": "x.y", "reason": "≤40字"}]}"""

PAIRWISE_SYSTEM = """你是一名资深合同律师，负责对同一需求下的两份合同做盲评对比（不知道作者）。
按给定维度逐一比较【合同A】与【合同B】，判断哪份在该维度上更好。
规则：
1. 只依据文本实际内容，不因篇幅长短本身给分；更长但含冗余/矛盾/错误的不算更好。
2. 用户需求中明确给出的数值/条件，用占位符替代或写错的一方在“需求符合度”上处于劣势。
3. 差距很小或各有优劣时判 tie。
4. 每个维度给出 winner（A/B/tie）和不超过60字的理由（指出具体条款）。
只输出 JSON：{"dimensions": [{"id": "维度ID", "winner": "A|B|tie", "reason": "..."}], "overall": "A|B|tie"}"""


@dataclass
class CheckItem:
    id: str
    dim: str
    text: str
    weight: float = 1.0


@dataclass
class ItemResult:
    id: str
    dim: str
    text: str
    weight: float
    votes: List[int] = field(default_factory=list)       # 1=yes 0=no（na 不计票）
    raw: List[dict] = field(default_factory=list)
    final: Optional[int] = None                          # 多数投票结果；None=na
    ungrounded: int = 0


class JudgeCache:
    def __init__(self, name: str):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.path = CACHE_DIR / f"{name}.jsonl"
        self._lock = threading.Lock()
        self._mem: Dict[str, dict] = {}
        # 读取所有缓存分片（judge.jsonl / judge_<case>.jsonl），任一分片命中即复用
        for p in sorted(CACHE_DIR.glob("judge*.jsonl")) if name.startswith("judge") else ([self.path] if self.path.exists() else []):
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                    self._mem[rec["key"]] = rec
                except Exception:
                    continue

    def get(self, key: str) -> Optional[dict]:
        return self._mem.get(key)

    def put(self, rec: dict):
        with self._lock:
            self._mem[rec["key"]] = rec
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class Judge:
    def __init__(self, model: str = "deepseek-flash", fallback: str = "deepseek-flash", samples: int = 3,
                 temperature: float = 0.0, cache_name: str = "judge", concurrency: int = 6,
                 rubric_version: str = "v1"):
        from agent.runtime.llm_gateway import LLMSettings
        self.settings = LLMSettings(model_id=model, fallback_model_id=fallback, temperature=temperature,
                                    timeout=420, idle_timeout=180, max_retries=4, fallback_after=3)
        self.model = model
        self.samples = samples
        self.cache = JudgeCache(cache_name)
        self.concurrency = concurrency
        self.rubric_version = rubric_version
        self.stats = {"calls": 0, "cache_hits": 0, "parse_failures": 0, "provider_errors": 0}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 底层调用（缓存 + 审计）
    def _call(self, system: str, user: str, sample: int, tag: str) -> dict:
        key = sha(f"{self.rubric_version}|{self.model}|{sha(system, 32)}|{sha(user, 32)}|{sample}", 32)
        hit = self.cache.get(key)
        if hit is not None and hit.get("parsed") is not None:
            with self._lock:
                self.stats["cache_hits"] += 1
            return hit
        from agent.runtime import call_llm, new_context, use_context
        from agent.utils.response_parser import ResponseParser
        ctx = new_context()
        t0 = time.time()
        raw, parsed, err = "", None, None
        with use_context(ctx):
            prompt = user
            for attempt in range(3):
                try:
                    res = call_llm(system, prompt, self.settings, agent=f"Judge:{tag}", json_mode=True,
                                   return_result=True)
                    raw = res.content
                    data = ResponseParser.parse_agent_response(raw)
                    if isinstance(data, dict) and not (set(data) == {"error"}):
                        parsed = data
                        model_used = res.model
                        break
                    prompt = user + "\n\n上一次输出不是合法 JSON，请严格只输出 JSON。"
                except Exception as e:  # noqa: BLE001
                    err = f"{type(e).__name__}: {e}"
                    from agent.runtime.errors import LLMFatalError
                    if isinstance(e, LLMFatalError):   # 余额不足 / 鉴权失败等：重试无意义，立即中止该调用
                        break
                    time.sleep(2)
        with self._lock:
            self.stats["calls"] += 1
            if parsed is None:
                self.stats["provider_errors" if err else "parse_failures"] += 1
        rec = {"key": key, "tag": tag, "sample": sample, "model": locals().get("model_used", self.model),
               "rubric_version": self.rubric_version, "system_hash": sha(system), "user_hash": sha(user),
               "raw": raw[:20000], "parsed": parsed, "error": err, "latency_s": round(time.time() - t0, 1),
               "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        if parsed is not None:
            self.cache.put(rec)
        return rec

    # ------------------------------------------------------------ 检查项评审
    def checklist(self, tag: str, text_with_locators: str, items: List[CheckItem], context: str = "",
                  ground_text: Optional[str] = None) -> Dict:
        """对一份文本按检查项评审 k 次，返回逐项多数投票结果 + 审计记录"""
        src_norm = normalize(ground_text if ground_text is not None else text_with_locators)
        results = {it.id: ItemResult(it.id, it.dim, it.text, it.weight) for it in items}
        audits = []

        def one(sample: int):
            order = list(items)
            random.Random(f"{tag}|{sample}").shuffle(order)
            listing = "\n".join(f"- [{it.id}] {it.text}" for it in order)
            user = (f"{context}\n\n【检查项】（共 {len(order)} 项，逐项输出，id 必须与方括号内一致）\n{listing}\n\n"
                    f"【被评文本】\n{text_with_locators}")
            return sample, self._call(JUDGE_SYSTEM, user, sample, tag)

        with ThreadPoolExecutor(max_workers=min(self.samples, self.concurrency)) as pool:
            outs = list(pool.map(one, range(self.samples)))
        for sample, rec in outs:
            parsed = rec.get("parsed") or {}
            got = {str(x.get("id")): x for x in parsed.get("items", []) if isinstance(x, dict)}
            audits.append({"sample": sample, "cache_key": rec["key"], "model": rec.get("model"),
                           "latency_s": rec.get("latency_s"), "error": rec.get("error")})
            for iid, r in results.items():
                x = got.get(iid)
                if x is None:
                    r.raw.append({"sample": sample, "verdict": "missing"})
                    continue
                v = str(x.get("verdict", "")).lower().strip()
                ev = str(x.get("evidence", "") or "")
                ok_ground = grounded(ev, src_norm) if v == "yes" else None
                entry = {"sample": sample, "verdict": v, "evidence": ev[:200], "locator": str(x.get("locator", "")),
                         "reason": str(x.get("reason", ""))[:120], "grounded": ok_ground}
                if v == "yes" and not ok_ground:
                    entry["verdict_final"] = "no(ungrounded)"
                    r.ungrounded += 1
                    r.votes.append(0)
                elif v == "yes":
                    r.votes.append(1)
                elif v == "no":
                    r.votes.append(0)
                r.raw.append(entry)
        for r in results.values():
            if r.votes:
                r.final = 1 if sum(r.votes) * 2 > len(r.votes) else 0
        return {"items": results, "audits": audits}

    # ------------------------------------------------------------ 成对比较（双向交换）
    def pairwise(self, tag: str, need: str, text_a: str, text_b: str, dims: List[dict]) -> Dict:
        listing = "\n".join(f"- [{d['id']}] {d['name']}：{d['desc']}" for d in dims)

        def build(x, y):
            return f"【用户需求】\n{need}\n\n【比较维度】\n{listing}\n\n【合同A】\n{x}\n\n【合同B】\n{y}"
        r1 = self._call(PAIRWISE_SYSTEM, build(text_a, text_b), 0, f"{tag}:AB")
        r2 = self._call(PAIRWISE_SYSTEM, build(text_b, text_a), 0, f"{tag}:BA")

        def winners(rec, swap):
            out = {}
            for d in (rec.get("parsed") or {}).get("dimensions", []) or []:
                w = str(d.get("winner", "tie")).upper()
                if swap:
                    w = {"A": "B", "B": "A"}.get(w, "TIE")
                out[str(d.get("id"))] = (w if w in ("A", "B") else "TIE", str(d.get("reason", ""))[:150])
            ov = str((rec.get("parsed") or {}).get("overall", "tie")).upper()
            if swap:
                ov = {"A": "B", "B": "A"}.get(ov, "TIE")
            out["__overall__"] = (ov if ov in ("A", "B") else "TIE", "")
            return out
        w1, w2 = winners(r1, False), winners(r2, True)
        result = {}
        for did in [d["id"] for d in dims] + ["__overall__"]:
            a, b = w1.get(did, ("TIE", "")), w2.get(did, ("TIE", ""))
            consistent = a[0] == b[0]
            result[did] = {"ab": a[0], "ba_swapped": b[0], "consistent": consistent,
                           "final": a[0] if consistent else "TIE", "reason_ab": a[1], "reason_ba": b[1]}
        return {"dims": result, "audits": [{"order": "AB", "cache_key": r1["key"]},
                                           {"order": "BA", "cache_key": r2["key"]}]}

    # ------------------------------------------------------------ 自由 JSON 调用（检索相关性等）
    def ask_json(self, tag: str, system: str, user: str, sample: int = 0) -> dict:
        return self._call(system, user, sample, tag)


def score_items(items: Dict[str, ItemResult]) -> Dict[str, dict]:
    """按维度汇总：score = Σ(weight·final) / Σ(weight)（na 不计）"""
    dims: Dict[str, dict] = {}
    for r in items.values():
        d = dims.setdefault(r.dim, {"num": 0.0, "den": 0.0, "n": 0, "pass": 0, "ungrounded": 0})
        d["ungrounded"] += r.ungrounded
        if r.final is None:
            continue
        d["num"] += r.weight * r.final
        d["den"] += r.weight
        d["n"] += 1
        d["pass"] += r.final
    for d in dims.values():
        d["score"] = d["num"] / d["den"] if d["den"] else float("nan")
    return dims


def items_to_json(items: Dict[str, ItemResult]) -> List[dict]:
    return [{"id": r.id, "dim": r.dim, "text": r.text, "weight": r.weight, "votes": r.votes, "final": r.final,
             "ungrounded": r.ungrounded, "raw": r.raw} for r in items.values()]
