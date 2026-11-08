"""工具 Harness：白名单 / 参数校验 / 事务回滚 / chunk 全局 locator"""
import copy
import json

from agent.utils.deal_chunk import split_contract_chunks
from agent.utils.outline_manager import OutlineManager
from agent.utils.tool_manager import ToolManager

TOOLS = "agent/agents/drafter_agent_tools.json"


def sample(n_sections=6):
    secs = []
    for i in range(1, n_sections + 1):
        secs.append({"章节编号": f"第{i}条", "章节标题": f"章{i}",
                     "条款列表": [{"条款编号": f"{i}.{j}", "条款大致说明": f"条{i}.{j}",
                               "子条款列表": [{"子条款编号": f"{i}.{j}.1", "子条款大致说明": f"子{i}.{j}.1"}]}
                              for j in (1, 2)]})
    return {"标准化模板文本": {"合同首部": "首部", "正文章节": secs, "合同尾部": "尾部"}}


def tm_for(data, root):
    import os
    os.chdir(root)
    om = OutlineManager(data=data)
    return om, ToolManager(om, tools_json=TOOLS)


def test_unknown_tool_rejected(ctx, request):
    om, tm = tm_for(sample(), request.config.rootpath)
    out = tm.run("os_system", {"cmd": "rm -rf /"})
    assert "未注册" in out and tm.harness.records[-1].error_code == "unknown_tool"


def test_locator_validation_and_no_mutation(ctx, request):
    data = sample()
    om, tm = tm_for(data, request.config.rootpath)
    before = json.dumps(om.get_outline(), ensure_ascii=False, sort_keys=True)
    out = tm.run("update_clause", {"locator": ["1.1", "9.9"], "content": ["新", "新"]})
    assert "locator_not_found" in out
    assert json.dumps(om.get_outline(), ensure_ascii=False, sort_keys=True) == before


def test_length_mismatch_and_empty(ctx, request):
    om, tm = tm_for(sample(), request.config.rootpath)
    assert "length_mismatch" in tm.run("update_clause", {"locator": ["1.1", "1.2"], "content": ["x"]})
    assert "empty_content" in tm.run("update_clause", {"locator": ["1.1"], "content": ["  "]})


def test_single_value_normalized(ctx, request):
    om, tm = tm_for(sample(), request.config.rootpath)
    out = tm.run("update_clause", {"locator": "1.1", "content": "改后"})
    assert "成功" in out and om.find_node("1.1")["条款大致说明"] == "改后"


def test_chunk_global_locator_insert(ctx, request):
    chunks = split_contract_chunks(data=copy.deepcopy(sample(6)), chunk_count=3)
    om, tm = tm_for(chunks[1], request.config.rootpath)  # 第 2 块：章节 3、4
    assert om.section_offset == 2
    out = tm.run("insert_clause", {"locators": ["3.1", "4"], "contents": ["新条款", "新章"],
                                   "positions": ["after", "after"]})
    assert "错误" not in out
    secs = om.get_outline()["标准化模板文本"]["正文章节"]
    assert [s["locator"] for s in secs] == ["3", "4", "5"]
    assert [c["条款大致说明"] for c in secs[0]["条款列表"]] == ["条3.1", "新条款", "条3.2"]
    assert om.validate_structure() == []


def test_delete_and_events(ctx, request):
    om, tm = tm_for(sample(), request.config.rootpath)
    out = tm.run("delete_clause" if "delete_clause" in tm.tools else "update_clause",
                 {"locator": ["1.1"], "content": ["x"]})
    assert "成功" in out
    kinds = [e["type"] for e in ctx.events]
    assert "tool.call" in kinds and "tool.result" in kinds and "contract.update" in kinds


def test_content_with_failure_words_is_ok(ctx, request):
    om, tm = tm_for(sample(), request.config.rootpath)
    tm.run("update_clause", {"locator": ["1.1"], "content": ["乙方无法履行时，视为履行失败并承担错误后果"]})
    assert tm.harness.records[-1].status == "ok"


def test_partial_batch(ctx, request):
    om, tm = tm_for(sample(), request.config.rootpath)
    tool = tm.tool_instances["update_clause"]
    real = tool.execute
    tool.execute = lambda **kw: real(**kw) + "\n未找到定位标识为 7.7 的节点"
    tm.run("update_clause", {"locator": ["1.1"], "content": ["x"]})
    assert tm.harness.records[-1].status == "partial"


def test_tool_exception_rolls_back(ctx, request, monkeypatch):
    data = sample()
    om, tm = tm_for(data, request.config.rootpath)
    tool = tm.tool_instances["update_clause"]

    def boom(**kw):
        om.get_outline()["标准化模板文本"]["正文章节"].clear()
        raise RuntimeError("crash midway")
    monkeypatch.setattr(tool, "execute", boom)
    out = tm.run("update_clause", {"locator": ["1.1"], "content": ["x"]})
    assert "执行失败" in out
    assert len(om.get_outline()["标准化模板文本"]["正文章节"]) == 6
