"""多轮 WebSearch 评测（对抗式大纲生成路径）

定义（可审计：每个检索结果的相关性判定都有 Judge 理由与缓存键）：
- 检索噪声率 noise_rate     = 被 Judge 判为“与该合同类型无关/无可用合同条款信息”的结果数 / 检索结果总数
  · 相关性用 LLM 二值判定（相关=包含该类合同的条款结构或条款内容），3 次采样多数决
- 检索利用率 utilization    = 被生成大纲“实际吸收”的相关结果数 / 相关结果数
  · 吸收判定：结果中出现的条款主题词（由 Judge 抽取 ≤8 个）至少 2 个出现在初始大纲（ModelA 输出）中
- 收敛轮数 rounds_to_converge = ModelB 判“完备”时的外循环轮数；未收敛记 max_loop
- 漏洞下降曲线 issues_per_round
"""
import json
from typing import Dict, List

from .common import normalize, render

REL_SYSTEM = """你是合同检索质量评审员。判断一条网页检索结果对“起草指定类型合同的大纲”是否有用。
有用(relevant=true)：包含该类合同的条款结构、条款要点或条款原文。
无用(false)：与该合同类型无关、仅广告/导航/资讯、仅泛泛普法而无条款信息。
同时从结果中抽取最多8个“条款主题词”（如：押金、维修责任、转租、违约金），无用结果返回空数组。
只输出 JSON：{"relevant": true|false, "topics": ["..."], "reason": "≤30字"}"""


def retrieval_metrics(judge, case_id: str, contract_type: str, retrievals: List[dict], initial_outline: dict,
                      trace: dict, max_loop: int = 3) -> Dict:
    results = []
    for ri, ret in enumerate(retrievals or []):
        for j, r in enumerate(ret.get("results", []) or []):
            if not (r.get("title") or r.get("content")):
                continue
            results.append({"round": ri, "idx": j, "title": r.get("title", ""), "url": r.get("url", ""),
                            "text": (r.get("summary", "") + "\n" + r.get("content", ""))[:1500]})
    outline_norm = normalize(render(initial_outline)) if initial_outline else ""
    rows = []
    for r in results:
        votes, topics, reasons, keys = [], [], [], []
        for s in range(judge.samples):
            rec = judge.ask_json(f"{case_id}:rel:{r['round']}:{r['idx']}", REL_SYSTEM,
                                 f"【合同类型】{contract_type}\n【检索结果标题】{r['title']}\n【检索结果内容】\n{r['text']}", s)
            p = rec.get("parsed") or {}
            votes.append(1 if p.get("relevant") is True else 0)
            topics.extend([str(t) for t in (p.get("topics") or [])][:8])
            reasons.append(str(p.get("reason", ""))[:60])
            keys.append(rec["key"])
        relevant = sum(votes) * 2 > len(votes)
        uniq = sorted({t for t in topics if 2 <= len(t) <= 12})
        absorbed_topics = [t for t in uniq if normalize(t) in outline_norm]
        absorbed = relevant and len(absorbed_topics) >= 2
        rows.append({**{k: r[k] for k in ("round", "idx", "title", "url")}, "votes": votes, "relevant": relevant,
                     "topics": uniq, "absorbed_topics": absorbed_topics, "absorbed": absorbed,
                     "reason": reasons[0] if reasons else "", "cache_keys": keys})
    n = len(rows)
    rel = [x for x in rows if x["relevant"]]
    rounds = (trace or {}).get("rounds", []) or []
    converged = bool((trace or {}).get("converged"))
    conv_round = next((x["round"] for x in rounds if x.get("issues") == 0), None)
    return {
        "results": n,
        "relevant": len(rel),
        "noise_rate": (1 - len(rel) / n) if n else float("nan"),
        "utilization": (sum(1 for x in rel if x["absorbed"]) / len(rel)) if rel else float("nan"),
        "converged": converged,
        "rounds_to_converge": conv_round if conv_round is not None else max_loop,
        "issues_per_round": [x.get("issues", 0) for x in rounds],
        "tool_calls_per_round": [x.get("tool_calls", 0) for x in rounds],
        "rows": rows,
        "relevance_votes": [x["votes"] for x in rows],
    }
