"""个人主页 / 免费额度 / 自有 API / 删除与中断的数据保留"""
import json
import threading
import time

import httpx
import pytest
from sqlalchemy import text

from test_server import _mysql_ok, auth, client, wait_status  # noqa: F401  复用 fixture

pytestmark = pytest.mark.skipif(not _mysql_ok(), reason="需要本地 MySQL")
REQ = "我是房东，上海个人对个人居住租赁，月租2500押一付三，允许养猫，违约金2个月租金。"


@pytest.fixture(autouse=True)
def _fake():
    from agent.runtime import set_mock_handler
    from fake_llm import FakeLLM
    fake = FakeLLM(delay=0.01)
    set_mock_handler(fake)
    from server.app import _hits
    _hits.clear()
    yield fake


def _count(sql, **kw):
    from server import db as sdb
    with sdb.engine().connect() as c:
        return c.execute(text(sql), kw).scalar()


def _finish(client, h, cid):
    client.post(f"/api/conversations/{cid}/messages", json={"text": REQ}, headers=h)
    wait_status(client, h, cid, {"waiting_input"})
    client.post(f"/api/conversations/{cid}/messages", json={"text": "确认"}, headers=h)
    d = wait_status(client, h, cid, {"waiting_input"})
    assert d["pipeline"]["current_step"] == 8
    client.post(f"/api/conversations/{cid}/messages", json={"text": "确认"}, headers=h)
    return wait_status(client, h, cid, {"completed"})


def test_delete_and_stop_keep_data(client):
    h = auth(client, "gina", "Passw0rd5")
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": REQ}, headers=h)
    wait_status(client, h, cid, {"waiting_input"})
    client.post(f"/api/conversations/{cid}/messages", json={"text": "确认"}, headers=h)
    time.sleep(0.3)
    client.post(f"/api/conversations/{cid}/stop", headers=h)
    wait_status(client, h, cid, {"paused", "waiting_input"})
    n_msg = _count("SELECT COUNT(*) FROM messages WHERE conversation_id=:c", c=cid)
    n_evt = _count("SELECT COUNT(*) FROM conversation_events WHERE conversation_id=:c", c=cid)
    n_ver = _count("SELECT COUNT(*) FROM contract_versions WHERE conversation_id=:c", c=cid)
    assert n_msg >= 2 and n_evt > 0 and n_ver >= 1, "中断后数据应完整保留"
    assert client.delete(f"/api/conversations/{cid}", headers=h).status_code == 200
    # 用户侧不可见
    assert client.get(f"/api/conversations/{cid}", headers=h).status_code == 404
    assert all(c["id"] != cid for c in client.get("/api/conversations", headers=h).json()["items"])
    # 数据库中软删除：会话行、消息、事件、合同版本全部保留
    assert _count("SELECT deleted FROM conversations WHERE id=:c", c=cid) == 1
    assert _count("SELECT COUNT(*) FROM messages WHERE conversation_id=:c", c=cid) == n_msg
    assert _count("SELECT COUNT(*) FROM conversation_events WHERE conversation_id=:c", c=cid) >= n_evt
    assert _count("SELECT COUNT(*) FROM contract_versions WHERE conversation_id=:c", c=cid) == n_ver


def test_free_quota_two_complete_runs(client, monkeypatch):
    from server import account
    monkeypatch.setattr(account, "FREE_RUNS", 2)
    h = auth(client, "henry", "Passw0rd6")
    q = client.get("/api/account", headers=h).json()["quota"]
    assert q["free_runs_left"] == 2 and q["free_tokens_left"] == q["free_tokens_total"]
    for _ in range(2):
        cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
        _finish(client, h, cid)
    q = client.get("/api/account", headers=h).json()["quota"]
    assert q["free_runs_used"] == 2 and q["exhausted"]
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    r = client.post(f"/api/conversations/{cid}/messages", json={"text": REQ}, headers=h)
    assert r.status_code == 402 and "免费额度已用完" in r.json()["detail"]


def test_token_quota_pauses_and_resumes_with_own_api(client, monkeypatch):
    """Token 额度耗尽 → 运行暂停（可恢复）；配置自有 API 后继续，不再消耗免费额度"""
    from server import account
    from agent.runtime import llm_gateway
    monkeypatch.setattr(account, "FREE_TOKENS", 1)
    real = llm_gateway._mock_handler

    def with_usage(req):
        from agent.runtime.llm_gateway import LLMResult
        out = real(req)
        return LLMResult(content=out, usage={"total_tokens": 50})
    llm_gateway.set_mock_handler(with_usage)
    h = auth(client, "ivy", "Passw0rd7")
    cid = client.post("/api/conversations", json={}, headers=h).json()["id"]
    client.post(f"/api/conversations/{cid}/messages", json={"text": REQ}, headers=h)
    d = wait_status(client, h, cid, {"paused"})
    assert "免费 Token 额度已用完" in (d["conversation"]["last_error"] or "") or \
        any(e["event_type"] == "run.failed" for e in d["events"])
    assert client.post(f"/api/conversations/{cid}/resume", headers=h).status_code == 402
    body = {"provider": "custom", "display_name": "我的模型", "api_format": "openai_chat",
            "base_url": "https://example.com/v1", "model": "my-model", "api_key": "sk-test-1234567890"}
    mid = client.post("/api/account/models", json=body, headers=h).json()["id"]
    acc = client.get("/api/account", headers=h).json()
    assert acc["active_model_id"] == mid and acc["models"][0]["api_key_hint"].endswith("7890")
    assert "sk-test-1234567890" not in json.dumps(acc), "接口不得返回明文 Key"
    used_before = acc["quota"]["free_tokens_used"]
    assert client.post(f"/api/conversations/{cid}/resume", headers=h).json()["ok"]
    wait_status(client, h, cid, {"waiting_input"})
    assert client.get("/api/account", headers=h).json()["quota"]["free_tokens_used"] == used_before
    from server import db as sdb
    with sdb.engine().connect() as c:
        enc = c.execute(text("SELECT api_key_enc FROM user_models WHERE id=:i"), {"i": mid}).scalar()
    assert "sk-test" not in enc, "数据库中 Key 必须加密存储"


def test_profile_password_avatar(client):
    h = auth(client, "jack", "Passw0rd8")
    png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    r = client.patch("/api/account/profile", json={"display_name": "杰克", "avatar": png}, headers=h)
    assert r.status_code == 200 and r.json()["user"]["avatar"] == png and r.json()["user"]["display_name"] == "杰克"
    assert client.patch("/api/account/profile", json={"avatar": "data:text/html;base64,PHNjcmlwdD4="},
                        headers=h).status_code == 400
    assert client.post("/api/account/password", json={"old_password": "wrong", "new_password": "Newpass99"},
                       headers=h).status_code == 400
    assert client.post("/api/account/password", json={"old_password": "Passw0rd8", "new_password": "Newpass99"},
                       headers=h).status_code == 200
    assert client.post("/api/auth/login", json={"username": "jack", "password": "Newpass99"}).status_code == 200


# ------------------------------------------------------------ 多协议适配（本地 mock 服务）
def _mock_server(handler):
    import socketserver
    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            status, chunks = handler(self.path, dict(self.headers), body)
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for ch in chunks:
                self.wfile.write(ch.encode())
                self.wfile.flush()

        def log_message(self, *a):
            pass
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_provider_formats():
    from agent.runtime.providers import Provider, stream_chat, test_connection
    seen = {}

    def handler(path, headers, body):
        seen[path.split("?")[0]] = (headers, body)
        if path.startswith("/nope"):
            return 404, ['{"error":"not found"}']
        if path.endswith("/chat/completions"):
            return 200, ['data: {"choices":[{"delta":{"reasoning_content":"想"}}]}\n\n',
                         'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n',
                         'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}\n\n',
                         "data: [DONE]\n\n"]
        if path.endswith("/responses"):
            return 200, ['event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"OK"}\n\n',
                         'event: response.completed\ndata: {"type":"response.completed","response":{"usage":{"input_tokens":3,"output_tokens":1,"total_tokens":4}}}\n\n']
        if path.endswith("/v1/messages"):
            return 200, ['event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":3}}}\n\n',
                         'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"OK"}}\n\n',
                         'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":1}}\n\n']
        if ":streamGenerateContent" in path:
            return 200, ['data: {"candidates":[{"content":{"parts":[{"text":"OK"}]}}],"usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":1,"totalTokenCount":4}}\n\n']
        return 404, ['{"error":"not found"}']
    srv, base = _mock_server(handler)
    try:
        msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
        for fmt, url in (("openai_chat", base + "/v1"), ("openai_responses", base + "/v1"), ("anthropic", base),
                         ("gemini", base)):
            out = list(stream_chat(Provider(fmt, url, "key-xyz", "m1"), msgs))
            assert "".join(v for k, v in out if k == "content") == "OK", fmt
            assert [v for k, v in out if k == "usage"][-1]["total_tokens"] == 4, fmt
        h, b = seen["/v1/messages"]
        assert h.get("x-api-key") == "key-xyz" and b["system"] == "SYS" and b["messages"][0]["role"] == "user"
        h, b = seen["/v1/responses"]
        assert b["instructions"] == "SYS" and h.get("Authorization") == "Bearer key-xyz"
        assert test_connection(Provider("openai_chat", base + "/v1", "k", "m"))["ok"]
        bad = test_connection(Provider("openai_chat", base + "/nope", "k", "m"))
        assert not bad["ok"] and "404" in bad["error"]
    finally:
        srv.shutdown()


def test_gateway_routes_to_user_provider():
    """运行上下文携带用户 provider 时，网关走用户 API（不使用平台 Key，不受平台模型白名单限制）"""
    from agent.runtime import call_llm, new_context, use_context, set_mock_handler
    from agent.runtime.providers import Provider
    set_mock_handler(None)
    srv, base = _mock_server(lambda p, h, b: (200, ['data: {"choices":[{"delta":{"content":"' + b["model"] + '"}}]}\n\n',
                                                     "data: [DONE]\n\n"]))
    try:
        ctx = new_context(tags={"provider": Provider("openai_chat", base + "/v1", "k", "claude-x")})
        with use_context(ctx):
            assert call_llm("s", "u") == "claude-x"
    finally:
        srv.shutdown()
