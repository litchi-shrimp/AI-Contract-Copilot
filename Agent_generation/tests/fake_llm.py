"""确定性 Fake LLM：按 agent 名称返回合法输出，用于无网络的全流程测试与故障注入"""
import json
import threading

OUTLINE = {
    "标准化模板文本": {
        "合同首部": "房屋租赁合同\n甲方：{出租方姓名}\n乙方：{承租方姓名}",
        "正文章节": [
            {"章节编号": "第一条", "章节标题": "租赁房屋", "条款列表": [
                {"条款编号": "1.1", "条款大致说明": "房屋坐落于{房屋地址}", "子条款列表": []}]},
            {"章节编号": "第二条", "章节标题": "租金与押金", "条款列表": [
                {"条款编号": "2.1", "条款大致说明": "月租金{月租金金额}元，押一付三", "子条款列表": []},
                {"条款编号": "2.2", "条款大致说明": "逾期支付宽限期5天", "子条款列表": []}]},
            {"章节编号": "第三条", "章节标题": "宠物条款", "条款列表": [
                {"条款编号": "3.1", "条款大致说明": "允许饲养一只猫", "子条款列表": []}]},
            {"章节编号": "第四条", "章节标题": "违约责任", "条款列表": [
                {"条款编号": "4.1", "条款大致说明": "违约金为2个月租金", "子条款列表": []}]},
            {"章节编号": "第五条", "章节标题": "争议解决", "条款列表": [
                {"条款编号": "5.1", "条款大致说明": "提交{房屋所在地}人民法院诉讼解决", "子条款列表": []}]},
        ],
        "合同尾部": "甲方签字：{出租方姓名}  乙方签字：{承租方姓名}",
    },
    "思考": "覆盖租金、宠物、违约等需求",
}


class FakeLLM:
    def __init__(self, fail: dict = None, delay: float = 0.0):
        """fail: {agent_substring: n} → 该 agent 前 n 次调用抛超时（故障注入）"""
        self.fail = dict(fail or {})
        self.calls = []
        self.delay = delay
        self._lock = threading.Lock()
        self._draft_turns = {}

    def __call__(self, req):
        import time
        import httpx
        import openai
        agent = req.get("agent") or ""
        with self._lock:
            self.calls.append(agent)
            for key, n in list(self.fail.items()):
                if key in agent and n > 0:
                    self.fail[key] = n - 1
                    raise openai.APITimeoutError(request=httpx.Request("POST", "http://fake"))
        if self.delay:
            time.sleep(self.delay)
        sys_p, user = req.get("system", ""), req.get("user", "")
        if agent == "UserFieldExtractionAgent":
            if "需求分析师" in sys_p:
                return "用户需要一份上海居住用房屋租赁合同，个人对个人，标准复杂度，含宠物与违约金条款。"
            return json.dumps({"matched_result": {}, "collected": {
                "contract_type": "房地产与物业管理类.房屋租赁合同（含长租公寓合同）", "party_type": "个人-个人",
                "region": "上海", "complexity": "标准", "scene": "居住"}, "is_stop": True,
                "new_extra_need": "允许养猫并约定赔偿", "response": "已为您确认全部信息。"}, ensure_ascii=False)
        if agent == "TemplateMatcher":
            return json.dumps({"indices": [0], "reason": "最相关"}, ensure_ascii=False)
        if agent in ("OutlineGenerationAgent", "AdversarialOutlineAgent") and "think" not in sys_p[:50]:
            if "ReAct" in sys_p or "think" in sys_p:
                return json.dumps({"think": "无需修改", "act": "final_answer", "parameter": {"answer_text": "完成"},
                                   "completed": True}, ensure_ascii=False)
            return json.dumps(OUTLINE, ensure_ascii=False)
        if agent == "ModelB_Reviewer":
            return "完备"
        if agent.startswith("DrafterAgent"):
            with self._lock:
                n = self._draft_turns.get(agent, 0)
                self._draft_turns[agent] = n + 1
            if n == 0:
                import re
                locs = re.findall(r"'locator': '(\d+\.\d+)'", user)
                if locs:
                    return json.dumps({"think": "扩写条款", "act": "use_tool", "parameter": {
                        "tool_name": "update_clause",
                        "tool_params": {"locator": [locs[0]], "content": [f"（已扩写）条款{locs[0]}的详细约定"]}},
                        "completed": False}, ensure_ascii=False)
            return json.dumps({"think": "完成", "act": "final_answer", "parameter": {"answer_text": "起草完成"},
                               "completed": True}, ensure_ascii=False)
        if agent.startswith("Review"):
            prefix = {"ReviewCompletenessAgent": "risk", "ReviewConsistencyAgent": "consistency",
                      "ReviewLegalAgent": "legal", "ReviewUsageAgent": "requirement"}.get(agent, "x")
            return json.dumps({"review_agent_name": agent, "problems": [{
                "review_agent_name": agent, "problem_id": f"{prefix}_1", "problem_type": "缺失",
                "problem_level": "中", "problem_description": "缺少维修责任约定", "impact_scope": "租赁房屋",
                "suggestions": ["增加维修责任条款"]}]}, ensure_ascii=False)
        if agent == "LeaderAgent":
            return json.dumps({"leader_agent_name": "LeaderAgent", "strategy_summary": "补充维修责任",
                               "decisions": [{"decision_id": "d1", "source_problem_ids": ["risk_1"],
                                              "tool_name": "insert_clause",
                                              "tool_params": {"locators": ["1.1"], "contents": ["维修责任由甲方承担"],
                                                              "positions": ["after"]},
                                              "priority": "P1", "decision_rationale": "补缺"}],
                               "deferred_items": []}, ensure_ascii=False)
        if agent in ("OutlineModificationAgent", "ContractModificationAgent"):
            return json.dumps({"think": "回答用户", "act": "answer_user",
                               "parameter": {"answer_text": "好的，已了解您的意见。"}, "completed": True},
                              ensure_ascii=False)
        return '{"ok": true}'
