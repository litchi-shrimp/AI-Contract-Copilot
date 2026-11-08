"""评测框架自身的正确性：证据核验 / 多数投票 / 双向成对 / κ / 缓存复现 / 工具轨迹指标"""
import json
import re

from agent.runtime import set_mock_handler
from evals.common import grounded, normalize
from evals.judge import CheckItem, Judge, score_items
from evals.stats import fleiss_kappa_binary, unanimity
from evals.tool_metrics import trajectory_metrics

TEXT = "[3.1] 房屋月租金为人民币2500元，押一付三。\n[6.1] 乙方可饲养一只猫。"


def test_grounded_requires_verbatim():
    src = normalize(TEXT)
    assert grounded("房屋月租金为人民币2500元", src)
    assert grounded("房屋月租金为人民币 2500 元，", src)  # 标点/空白归一化
    assert not grounded("月租金为人民币3000元", src)
    assert not grounded("租金", src)  # 过短


def test_checklist_majority_and_ungrounded(tmp_path, monkeypatch):
    import evals.judge as J
    monkeypatch.setattr(J, "CACHE_DIR", tmp_path)
    calls = {"n": 0}

    def fake(req):
        calls["n"] += 1
        s = calls["n"]
        items = [
            {"id": "A", "verdict": "yes", "evidence": "房屋月租金为人民币2500元", "locator": "3.1"},
            # B：模型声称通过，但证据是编造的 → 必须判为不通过
            {"id": "B", "verdict": "yes", "evidence": "违约金为两个月租金", "locator": "9.1"},
            # C：3 次中 2 次 yes → 多数通过
            {"id": "C", "verdict": "yes" if s != 2 else "no", "evidence": "乙方可饲养一只猫", "locator": "6.1"},
        ]
        return json.dumps({"items": items}, ensure_ascii=False)
    set_mock_handler(fake)
    j = Judge(samples=3, cache_name="t1", rubric_version="t")
    items = [CheckItem("A", "D1", "月租金"), CheckItem("B", "D1", "违约金"), CheckItem("C", "D2", "宠物")]
    res = j.checklist("case", TEXT, items)["items"]
    assert res["A"].final == 1 and res["A"].votes == [1, 1, 1]
    assert res["B"].final == 0 and res["B"].ungrounded == 3
    assert res["C"].final == 1 and sorted(res["C"].votes) == [0, 1, 1]
    dims = score_items(res)
    assert abs(dims["D1"]["score"] - 0.5) < 1e-9 and dims["D2"]["score"] == 1.0

    # 缓存复现：第二次运行不再调用模型，结果完全一致
    before = calls["n"]
    j2 = Judge(samples=3, cache_name="t1", rubric_version="t")
    res2 = j2.checklist("case", TEXT, items)["items"]
    assert calls["n"] == before and j2.stats["cache_hits"] == 3
    assert {k: v.votes for k, v in res2.items()} == {k: v.votes for k, v in res.items()}


def test_pairwise_position_swap(tmp_path, monkeypatch):
    import evals.judge as J
    monkeypatch.setattr(J, "CACHE_DIR", tmp_path)

    def fake(req):
        u = req["user"]
        # 维度 P1：总是偏好“合同A”位置（位置偏差）→ 交换后不一致 → 平局
        # 维度 P2：真实偏好包含“猫”的文本 → 交换后一致
        a_part = u.split("【合同A】")[1].split("【合同B】")[0]
        p2 = "A" if "猫" in a_part else "B"
        return json.dumps({"dimensions": [{"id": "P1", "winner": "A"}, {"id": "P2", "winner": p2}],
                           "overall": p2}, ensure_ascii=False)
    set_mock_handler(fake)
    j = Judge(samples=1, cache_name="t2", rubric_version="t")
    r = j.pairwise("c", "需求", "含猫的合同", "普通合同", [{"id": "P1", "name": "x", "desc": ""},
                                                       {"id": "P2", "name": "y", "desc": ""}])["dims"]
    assert r["P1"]["final"] == "TIE" and not r["P1"]["consistent"]
    assert r["P2"]["final"] == "A" and r["P2"]["consistent"]
    assert r["__overall__"]["final"] == "A"


def test_kappa():
    assert abs(fleiss_kappa_binary([[1, 1, 1], [0, 0, 0], [1, 1, 1], [0, 0, 0]]) - 1.0) < 1e-9
    assert fleiss_kappa_binary([[1, 0, 1], [0, 1, 0], [1, 0, 0], [0, 1, 1]]) < 0.1
    assert unanimity([[1, 1], [1, 0]]) == 0.5


def test_trajectory_metrics():
    recs = [
        {"agent": "d", "tool": "update_clause", "params": {"locator": ["1.1", "1.2"]}, "status": "ok", "batch_size": 2,
         "locators": ["1.1", "1.2"]},
        {"agent": "d", "tool": "update_clause", "params": {"locator": ["9.9"]}, "status": "rejected",
         "error_code": "locator_not_found", "batch_size": 1, "locators": ["9.9"]},
        {"agent": "d", "tool": "update_clause", "params": {"locator": ["9.9"]}, "status": "rejected",
         "error_code": "locator_not_found", "batch_size": 1, "locators": ["9.9"]},
        {"agent": "d", "tool": "update_clause", "params": {"locator": ["1.1"], "x": 1}, "status": "ok", "batch_size": 1,
         "locators": ["1.1"]},
    ]
    m = trajectory_metrics(recs)
    assert m["calls"] == 4 and m["invalid_rate"] == 0.5
    assert m["batch_utilization"] == 0.5
    assert m["detour_breakdown"]["rewrite_same_locator"] == 1 and m["detour_breakdown"]["duplicate_call"] == 1
    assert m["repeat_failure_rate"] == 0.5 and m["self_correction_rate"] == 0.5
