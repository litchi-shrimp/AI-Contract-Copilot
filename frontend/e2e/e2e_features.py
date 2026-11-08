"""E2E（新增功能）：并行阶段折叠 / 拖拽布局 / 个人主页（资料·密码·模型配置·连通性测试）
    python e2e/e2e_features.py
"""
import json, os, re, sys, threading, time, uuid, socketserver
from http.server import BaseHTTPRequestHandler
from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:4173"
SHOTS = os.path.join(os.path.dirname(__file__), "shots")
os.makedirs(SHOTS, exist_ok=True)
REQ = "我是房东，需要一份上海松江区住房租赁合同（个人对个人、居住），租期1年，月租2500元押一付三，允许养一只猫，违约金2个月租金。"


class MockOpenAI(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        ok = self.headers.get("Authorization") == "Bearer sk-good-key-123456"
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "text/event-stream" if ok else "application/json")
        self.end_headers()
        if ok:
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')
        else:
            self.wfile.write(b'{"error":{"message":"Incorrect API key provided"}}')
    def log_message(self, *a): pass


srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), MockOpenAI)
threading.Thread(target=srv.serve_forever, daemon=True).start()
MOCK = f"http://127.0.0.1:{srv.server_address[1]}/v1"
errors = []

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_context(viewport={"width": 1600, "height": 960}, locale="zh-CN").new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and "402" not in m.text and errors.append(m.text))
    page.goto(BASE + "/#/login")
    page.get_by_role("tab", name="注册").click()
    user = "f_" + uuid.uuid4().hex[:6]
    page.get_by_test_id("username").fill(user)
    page.get_by_test_id("password").fill("Passw0rd!"); page.get_by_test_id("password2").fill("Passw0rd!")
    page.get_by_test_id("submit").click()
    expect(page.get_by_text("今天要起草什么合同？")).to_be_visible()
    expect(page.locator(".disclaimer")).to_contain_text("剩余")

    # ---- (2) 拖拽：侧栏与文档面板宽度可调并持久化
    side = page.locator("nav.side")
    w0 = side.bounding_box()["width"]
    sp = page.locator(".splitter").first.bounding_box()
    page.mouse.move(sp["x"] + 3, sp["y"] + 300); page.mouse.down(); page.mouse.move(sp["x"] + 83, sp["y"] + 300, steps=6); page.mouse.up()
    w1 = side.bounding_box()["width"]
    assert w1 - w0 > 60, (w0, w1)

    # ---- (1) 并行阶段折叠：Step 5/6 只显示一条“起草中 / 审查中”
    page.get_by_test_id("composer").fill(REQ); page.get_by_test_id("send").click()
    expect(page.get_by_test_id("confirm")).to_be_visible(timeout=60000)
    panel = page.locator("aside.panel").bounding_box()
    sp2 = page.locator(".splitter").nth(1).bounding_box()
    page.mouse.move(sp2["x"] + 3, sp2["y"] + 300); page.mouse.down(); page.mouse.move(sp2["x"] - 117, sp2["y"] + 300, steps=6); page.mouse.up()
    assert page.locator("aside.panel").bounding_box()["width"] - panel["width"] > 80
    page.get_by_test_id("confirm").click()
    expect(page.locator(".parallel", has_text="起草中")).to_be_visible(timeout=30000)
    page.screenshot(path=os.path.join(SHOTS, "f1_drafting.png"))
    expect(page.locator(".parallel", has_text=re.compile("审查(中|完成)"))).to_be_visible(timeout=60000)
    page.screenshot(path=os.path.join(SHOTS, "f2_reviewing.png"))
    expect(page.get_by_test_id("confirm")).to_be_visible(timeout=90000)
    # 第 5/6 步区间（从“合同起草”步骤块到“修订”步骤块之间）不应出现任何思考 / 工具明细块
    info = page.evaluate("""() => {
      const body = [...document.querySelectorAll('.turn .body')].find(b => b.textContent.includes('合同起草'));
      const kids = [...body.children]; let on = false, think = 0, tool = 0;
      for (const k of kids) {
        if (k.classList.contains('step') && k.textContent.includes('合同起草')) on = true;
        else if (k.classList.contains('step') && k.textContent.includes('修订')) on = false;
        else if (on && k.matches('details.think')) think++;
        else if (on && k.matches('details.tool') && !k.textContent.includes('审查发现')) tool++;
      }
      return {think, tool};
    }""")
    assert info == {"think": 0, "tool": 0}, ("并行阶段不应渲染思考/工具明细", info)
    assert page.locator(".parallel").count() == 2
    expect(page.locator(".parallel").first).to_contain_text("起草完成")
    nodes = page.evaluate("document.querySelectorAll('*').length")
    print("DOM nodes after step 7:", nodes)
    page.screenshot(path=os.path.join(SHOTS, "f3_after.png"))

    # ---- (4) 个人主页
    page.get_by_test_id("profile").click()
    expect(page).to_have_url(re.compile(r"#/me"))
    expect(page.locator(".quota")).to_contain_text("免费完整生成")
    page.get_by_test_id("add-model").click()
    page.get_by_test_id("prov-custom").click()
    page.get_by_test_id("api-format").select_option("openai_chat")
    page.get_by_test_id("base-url").fill(MOCK)
    page.get_by_test_id("model-id").fill("mock-gpt")
    page.get_by_test_id("api-key").fill("sk-wrong")
    page.get_by_test_id("test-btn").click()
    expect(page.get_by_test_id("test-result")).to_contain_text("连接失败", timeout=20000)
    expect(page.get_by_test_id("test-result")).to_contain_text("401")
    page.get_by_test_id("api-key").fill("sk-good-key-123456")
    page.get_by_test_id("test-btn").click()
    expect(page.get_by_test_id("test-result")).to_contain_text("连接成功", timeout=20000)
    page.screenshot(path=os.path.join(SHOTS, "f4_model_dialog.png"))
    page.get_by_test_id("save-model").click()
    expect(page.locator(".mitem.active")).to_contain_text("mock-gpt")
    expect(page.locator(".mitem.active")).to_contain_text("…3456")
    # Anthropic 预置：自动填充 Base URL / 格式
    page.get_by_test_id("add-model").click()
    page.get_by_test_id("prov-anthropic").click()
    assert page.get_by_test_id("base-url").input_value() == "https://api.anthropic.com"
    assert page.get_by_test_id("api-format").input_value() == "anthropic"
    page.get_by_role("button", name="取消").click()
    page.screenshot(path=os.path.join(SHOTS, "f5_profile_models.png"))
    # 改密码
    page.get_by_role("tab", name="账号安全").click()
    page.get_by_test_id("old-pw").fill("Passw0rd!"); page.get_by_test_id("new-pw").fill("Newpass99"); page.get_by_test_id("new-pw2").fill("Newpass99")
    page.get_by_test_id("save-pw").click()
    expect(page.get_by_text("密码已修改")).to_be_visible()
    # 头像
    page.get_by_role("tab", name="基本资料").click()
    png = os.path.join(SHOTS, "f1_drafting.png")
    page.get_by_test_id("avatar-input").set_input_files(png)
    expect(page.locator(".av-wrap img.av")).to_be_visible(timeout=10000)
    page.get_by_test_id("back").click()
    expect(page.locator(".me-btn img.av")).to_be_visible()
    expect(page.locator(".disclaimer")).to_contain_text("正在使用你配置的模型 API")
    b.close()
srv.shutdown()
print("errors:", errors)
print("E2E FEATURES PASSED" if not errors else "PASSED WITH CONSOLE ERRORS")
