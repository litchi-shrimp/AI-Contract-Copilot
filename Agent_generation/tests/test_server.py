"""平台后端 API 测试（真实 MySQL 测试库 + Fake LLM）

覆盖：注册/登录/鉴权、用户隔离、发送消息→运行→等待输入、停止（后端真正停止）→继续（检查点恢复）、
幂等消息、完成后会话关闭拒绝输入、SSE 事件流、docx 导出
"""
import json
import os
import threading
import time

import pytest

TEST_DB = os.getenv("TEST_SERVER_DATABASE_URL",
                    "mysql+pymysql://root:123456@127.0.0.1:3306/contract_copilot_test?charset=utf8mb4")


def _mysql_ok():
    try:
        import pymysql
        pymysql.connect(host="127.0.0.1", user="root", password="123456", connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mysql_ok(), reason="需要本地 MySQL")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["DATABASE_URL"] = TEST_DB
    os.environ["WORKSPACES_DIR"] = str(tmp_path_factory.mktemp("ws"))
    import importlib
    from server import config
    importlib.reload(config)
    config.settings.database_url = TEST_DB
    config.settings.workspaces_dir = tmp_path_factory.mktemp("ws2")
    from server import db as sdb
    sdb._engine = None
    from db.init_db import init_db
    init_db(TEST_DB)
    from sqlalchemy import text
    with sdb.engine().begin() as c:
        for t in ("messages", "conversation_events", "contract_versions", "conversations", "refresh_tokens",
                  "audit_log", "users"):
            c.execute(text(f"DELETE FROM {t}"))
    from fastapi.testclient import TestClient
    from server.app import app, _hits
    _hits.clear()
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    from agent.runtime import set_mock_handler
    from fake_llm import FakeLLM
    fake = FakeLLM(delay=0.02)
    set_mock_handler(fake)
    from server.app import _hits
    _hits.clear()
    yield fake


def auth(client, name="alice", pw="Passw0rd!"):
    r = client.post("/api/auth/register", json={"username": name, "password": pw})
    if r.status_code == 409:
        r = client.post("/api/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def wait_status(client, h, cid, statuses, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = client.get(f"/api/conversations/{cid}", headers=h).json()
        if d["conversation"]["status"] in statuses and not (d["conversation"]["running"] and "running" not in statuses
                                                           and d["conversation"]["status"] == "running"):
            return d
        time.sleep(0.1)
    raise AssertionError(f"timeout waiting {statuses}, last={d['conversation']}")


def test_auth_validation_and_lockout(client):
    assert client.post("/api/auth/register", json={"username": "ab", "password": "x"}).status_code == 400
    assert client.post("/api/auth/register", json={"username": "bob", "password": "allletters"}).status_code == 400
    auth(client, "bob", "Secret123")
    assert client.get("/api/conversations").status_code == 401
    assert client.get("/api/conversations", headers={"Authorization": "Bearer xxx"}).status_code == 401
    from server.app import _hits
    codes = []
    for _ in range(6):
        _hits.clear()  # 隔离 IP 限流，只测账号锁定
        codes.append(client.post("/api/auth/login", json={"username": "bob", "password": "Wrong1234"}).status_code)
    # 5 次失败后锁定；锁定后即使密码正确也拒绝
    assert codes[:5] == [401] * 5 and codes[5] == 423
    _hits.clear()
    assert client.post("/api/auth/login", json={"username": "bob", "password": "Secret123"}).status_code == 423


def test_full_flow_stop_resume_close(client, _fake):
    h = auth(client)
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    req = "我是房东，上海个人对个人居住租赁，月租2500押一付三，允许养猫，违约金2个月租金。"
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": req, "client_msg_id": "m1"}, headers=h)
    assert r.status_code == 200, r.text
    # 幂等：同一 client_msg_id 重复提交只记一条
    r2 = client.post(f"/api/conversations/{cid}/messages", json={"text": req, "client_msg_id": "m1"}, headers=h)
    assert r2.json().get("duplicate") is True
    d = wait_status(client, h, cid, {"waiting_input"})
    assert d["pipeline"]["current_step"] == 4 and d["contract"]["kind"] == "outline"
    assert d["conversation"]["title"].startswith("我是房东")
    _fake.delay = 0.3   # 放慢起草阶段，保证“停止”一定落在 Step 5 进行中（避免测试时序竞争）
    client.post(f"/api/conversations/{cid}/messages", json={"text": "确认", "client_msg_id": "m2"}, headers=h)
    # 起草阶段中途停止：后端必须真正停止
    deadline = time.time() + 20
    while time.time() < deadline and not any(c.startswith("DrafterAgent") for c in _fake.calls):
        time.sleep(0.02)
    r = client.post(f"/api/conversations/{cid}/stop", headers=h)
    assert r.json()["stopped"] is True
    d = wait_status(client, h, cid, {"paused"})
    assert not d["conversation"]["running"]
    n_calls = len(_fake.calls)
    time.sleep(0.5)
    assert len(_fake.calls) == n_calls, "停止后后端不应继续调用 LLM"
    assert 5 not in d["pipeline"]["step_done"]
    _fake.delay = 0.02
    # 停止期间不允许发消息之外的并发运行；继续 → 从检查点恢复
    assert client.post(f"/api/conversations/{cid}/resume", headers=h).json()["ok"]
    d = wait_status(client, h, cid, {"waiting_input"}, timeout=40)
    assert d["pipeline"]["current_step"] == 8 and 5 in d["pipeline"]["step_done"]
    resumed = _fake.calls[n_calls:]
    assert "UserFieldExtractionAgent" not in resumed and "OutlineGenerationAgent" not in resumed
    # docx 导出
    r = client.get(f"/api/conversations/{cid}/export.docx", headers=h)
    assert r.status_code == 200 and r.content[:2] == b"PK"
    # 终稿确认 → 会话关闭
    client.post(f"/api/conversations/{cid}/messages", json={"text": "确认", "client_msg_id": "m3"}, headers=h)
    d = wait_status(client, h, cid, {"completed"})
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": "再改一下"}, headers=h)
    assert r.status_code == 409
    assert client.post(f"/api/conversations/{cid}/resume", headers=h).status_code == 409
    kinds = {e["event_type"] for e in d["events"]}
    # 历史事件落库（刷新页面可回放思考过程 / 工具调用）
    assert {"step.start", "step.complete", "tool.result", "agent.think.text"} <= kinds, kinds
    roles = [m["role"] for m in d["messages"]]
    assert roles.count("user") == 3


def test_user_isolation(client):
    h1 = auth(client, "carol", "Passw0rd1")
    h2 = auth(client, "dave", "Passw0rd2")
    cid = client.post("/api/conversations", json={"title": "私有"}, headers=h1).json()["id"]
    assert client.get(f"/api/conversations/{cid}", headers=h2).status_code == 404
    assert client.post(f"/api/conversations/{cid}/stop", headers=h2).status_code == 404
    assert client.get(f"/api/conversations/{cid}/export.docx", headers=h2).status_code == 404
    assert all(c["id"] != cid for c in client.get("/api/conversations", headers=h2).json()["items"])


def test_sse_stream(client):
    """真实 uvicorn + httpx 流式读取（TestClient 无法模拟客户端断开）"""
    import httpx
    import uvicorn
    from server.app import app
    h = auth(client, "erin", "Passw0rd3")
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    token = h["Authorization"].split()[1]
    cfg = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning", lifespan="off")
    srv = uvicorn.Server(cfg)
    st = threading.Thread(target=srv.run, daemon=True)
    st.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    got = []

    def reader():
        with httpx.stream("GET", f"http://127.0.0.1:8765/api/conversations/{cid}/events?token={token}",
                          timeout=30) as resp:
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    got.append(json.loads(line[6:]))
                if any(e["event_type"] == "run.waiting" for e in got):
                    break
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    time.sleep(0.5)
    client.post(f"/api/conversations/{cid}/messages", json={"text": "上海个人租赁合同，月租2500"}, headers=h)
    th.join(30)
    # 断线重连：携带 Last-Event-ID 只补发之后的事件
    last = got[2]["id"]
    replay = []
    with httpx.stream("GET", f"http://127.0.0.1:8765/api/conversations/{cid}/events?token={token}",
                      headers={"Last-Event-ID": str(last)}, timeout=10) as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                replay.append(json.loads(line[6:]))
            if len(replay) >= 3:
                break
    srv.should_exit = True
    st.join(5)
    assert replay and replay[0]["id"] == last + 1
    types = [e["event_type"] for e in got]
    assert "run.started" in types and "step.start" in types and "run.waiting" in types
    assert any(t in types for t in ("agent.think.stream", "llm.token"))
    ids = [e["id"] for e in got]
    assert ids == sorted(ids)
    wait_status(client, h, cid, {"waiting_input"})


def test_input_sanitization_and_limits(client):
    h = auth(client, "frank", "Passw0rd4")
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    assert client.post(f"/api/conversations/{cid}/messages", json={"text": "\x00\x01  "}, headers=h).status_code == 400
    assert client.post(f"/api/conversations/{cid}/messages", json={"text": "x" * 9000}, headers=h).status_code == 422
