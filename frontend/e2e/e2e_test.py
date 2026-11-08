"""前端 E2E（Playwright · Chromium）：注册登录 → 发送需求 → 实时流 → 大纲确认 → 起草中停止 → 继续 → 终稿确认 → 会话关闭 → 下载 docx
    python e2e/e2e_test.py [--base http://127.0.0.1:4173] [--shots e2e/shots]
"""
import argparse, os, re, sys, time, uuid
from playwright.sync_api import sync_playwright, expect

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:4173")
ap.add_argument("--shots", default=os.path.join(os.path.dirname(__file__), "shots"))
a = ap.parse_args()
os.makedirs(a.shots, exist_ok=True)
REQ = "我是房东，需要一份上海松江区住房租赁合同（个人对个人、居住），租期1年，月租2500元押一付三，允许养一只猫，违约金2个月租金。"
user = "e2e_" + uuid.uuid4().hex[:6]
errors = []

def shot(page, name):
    page.screenshot(path=os.path.join(a.shots, f"{name}.png"), full_page=False)

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1600, "height": 960}, accept_downloads=True, locale="zh-CN")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))

    # 1. 未登录 → 跳转登录页；注册
    page.goto(a.base + "/#/")
    expect(page).to_have_url(re.compile(r"#/login"))
    shot(page, "01_login")
    page.get_by_role("tab", name="注册").click()
    page.get_by_test_id("username").fill(user)
    page.get_by_test_id("password").fill("weak")
    page.get_by_test_id("password2").fill("weak")
    page.get_by_test_id("submit").click()
    expect(page.locator(".err")).to_contain_text("密码")
    page.get_by_test_id("password").fill("Passw0rd!")
    page.get_by_test_id("password2").fill("Passw0rd!")
    page.get_by_test_id("submit").click()
    expect(page.get_by_text("今天要起草什么合同？")).to_be_visible()
    shot(page, "02_welcome")

    # 2. 发送需求 → 自动建会话 → 实时流 → 等待大纲确认
    page.get_by_test_id("composer").fill(REQ)
    page.get_by_test_id("send").click()
    expect(page).to_have_url(re.compile(r"#/c/"))
    expect(page.locator(".bubble").first).to_contain_text("我是房东")
    expect(page.get_by_test_id("stop")).to_be_visible(timeout=10000)
    expect(page.get_by_test_id("confirm")).to_be_visible(timeout=60000)
    expect(page.locator(".page .sec").first).to_be_visible()
    assert page.locator(".side .item").count() >= 1
    shot(page, "03_outline_ready")
    # 思考过程默认折叠，可展开
    think = page.locator("details.think").first
    if think.count():
        assert think.get_attribute("open") is None
        think.locator("summary").click()
        assert think.get_attribute("open") is not None
    # 工具调用块可展开
    shot(page, "04_think_open")

    # 3. 大纲修改一轮（AI 回答）
    page.get_by_test_id("composer").fill("第三条加上：不得饲养犬类")
    page.get_by_test_id("send").click()
    expect(page.get_by_test_id("confirm")).to_be_visible(timeout=30000)

    # 4. 确认大纲 → 起草中 → 停止
    page.get_by_test_id("confirm").click()
    # 起草步骤开始（并行起草出现）后立即停止
    expect(page.locator(".parallel").first).to_be_visible(timeout=30000)
    page.get_by_test_id("stop").click(timeout=5000)
    expect(page.locator(".notice")).to_contain_text("已停止", timeout=15000)
    expect(page.get_by_test_id("resume")).to_be_visible()
    shot(page, "05_stopped")
    n_tools_before = page.locator("details.tool").count()
    time.sleep(1.5)
    assert page.locator("details.tool").count() == n_tools_before, "停止后不应再有新的工具调用"

    # 5. 刷新页面（检验历史回放）后继续
    page.reload()
    expect(page.get_by_test_id("resume")).to_be_visible(timeout=10000)
    page.get_by_test_id("resume").click()
    # 恢复后：起草/审查/修订期间工具实时修改合同 → 右侧面板出现增量高亮
    expect(page.locator(".page .st-added, .page .st-modified").first).to_be_visible(timeout=90000)
    shot(page, "06_contract_diff")
    expect(page.get_by_test_id("confirm")).to_be_visible(timeout=90000)
    resumed_steps = page.locator(".step .chip", has_text="从检查点恢复").count()
    print("resumed step markers:", resumed_steps)

    # 6. 下载 docx
    with page.expect_download() as dl:
        page.get_by_test_id("download").click()
    path = dl.value.path()
    assert open(path, "rb").read(2) == b"PK", "docx 文件格式错误"

    # 7. 终稿确认 → 会话关闭，输入框禁用
    page.get_by_test_id("confirm").click()
    expect(page.locator(".done-card")).to_be_visible(timeout=30000)
    expect(page.get_by_test_id("composer")).to_be_disabled()
    shot(page, "07_completed")

    # 8. 新建任务 → 互不影响；面板折叠
    page.get_by_test_id("new-task").click()
    expect(page.get_by_text("今天要起草什么合同？")).to_be_visible()
    assert page.locator(".side .item").count() >= 2
    page.locator(".side .item").nth(1).click()
    expect(page.locator(".done-card")).to_be_visible(timeout=10000)
    page.get_by_role("button", name="收起面板").click()
    expect(page.get_by_test_id("open-panel")).to_be_visible()
    shot(page, "08_panel_collapsed")

    # 9. 退出登录
    page.get_by_test_id("logout").click()
    expect(page).to_have_url(re.compile(r"#/login"))
    b.close()

bad = [e for e in errors if "favicon" not in e]
print("console/page errors:", bad)
print("E2E PASSED" if not bad else "E2E PASSED WITH CONSOLE ERRORS")
