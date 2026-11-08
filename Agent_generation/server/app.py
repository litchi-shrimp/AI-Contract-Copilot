"""FastAPI 应用

    uvicorn server.app:app --host 0.0.0.0 --port 8000

API（全部需要 Bearer Token，除 /api/auth/* 与 /api/health）：
  POST   /api/auth/register          注册
  POST   /api/auth/login             登录 → token
  GET    /api/auth/me
  GET    /api/conversations          历史会话
  POST   /api/conversations          新建任务
  GET    /api/conversations/{id}     会话详情（消息 / 状态 / 最新合同 / 历史事件）
  PATCH  /api/conversations/{id}     重命名
  DELETE /api/conversations/{id}     删除（软删除，先停止运行）
  POST   /api/conversations/{id}/messages   发送消息（幂等键 client_msg_id）→ 启动/继续运行
  POST   /api/conversations/{id}/stop       停止（后端立即取消，检查点保留）
  POST   /api/conversations/{id}/resume     继续（从最后检查点恢复）
  GET    /api/conversations/{id}/events     SSE 实时事件（支持 Last-Event-ID 断线重连）
  GET    /api/conversations/{id}/export.docx  下载 Word
"""
import asyncio
import json
import logging
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import db
from .auth import CurrentUser, hash_password, issue_token, validate_credentials, verify_password
from .config import settings
from .docx_export import contract_to_docx, safe_filename
from .runs import QuotaExhausted, manager, sanitize_input

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("server")


# ------------------------------------------------------------ 生命周期
def _janitor(stop: threading.Event):
    """后台回收：每 60s 释放闲置会话的内存（等待输入超时 / 已结束的运行、无人订阅的事件通道）；每 10 分钟关闭过期会话"""
    tick = 0
    while not stop.wait(60):
        tick += 1
        try:
            r = manager.gc()
            if r["evicted"] or r["channels_dropped"]:
                log.info("gc: %s", r)
            if tick % 10 == 0:
                n = db.close_idle(settings.idle_close_hours)
                if n:
                    log.info("closed %s idle conversations", n)
        except Exception as e:  # noqa: BLE001
            log.warning("janitor error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    n = db.recover_interrupted_runs()
    if n:
        log.info("recovered %s interrupted runs → paused", n)
    stop = threading.Event()
    threading.Thread(target=_janitor, args=(stop,), daemon=True).start()
    yield
    stop.set()
    manager.shutdown()


app = FastAPI(title="AI Contract Copilot API", version="2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_origin_regex=settings.cors_origin_regex,
                   allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type", "Last-Event-ID"])


# ------------------------------------------------------------ 简易限流（按 IP + 路由组，滑动窗口）
_hits = defaultdict(deque)
_hits_lock = threading.Lock()
LIMITS = {"auth": (10, 60), "msg": (30, 60)}


def rate_limit(group: str):
    def dep(request: Request):
        n, window = LIMITS[group]
        key = f"{group}:{request.client.host if request.client else '-'}"
        now = time.time()
        with _hits_lock:
            q = _hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= n:
                raise HTTPException(429, "请求过于频繁，请稍后再试")
            q.append(now)
    return Depends(dep)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "服务器内部错误"}, status_code=500)


# ------------------------------------------------------------ 模型
class Credentials(BaseModel):
    username: str = Field(..., max_length=32)
    password: str = Field(..., max_length=72)
    display_name: Optional[str] = Field(None, max_length=32)


class NewConversation(BaseModel):
    title: Optional[str] = Field(None, max_length=200)


class Rename(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class Message(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)
    client_msg_id: Optional[str] = Field(None, max_length=64)


def _conv_or_404(user: dict, cid: str, with_state: bool = False) -> dict:
    conv = db.get_conversation(user["id"], cid, with_state=with_state)
    if not conv:
        raise HTTPException(404, "会话不存在")
    return conv


def _public_conv(conv: dict) -> dict:
    out = {k: conv[k] for k in ("id", "title", "status", "current_step", "created_at", "updated_at") if k in conv}
    out["last_error"] = conv.get("last_error")
    out["running"] = manager.is_running(conv["id"])
    return out


# ------------------------------------------------------------ 认证
@app.get("/api/health")
def health():
    from agent.repository import get_template_repository
    return {"ok": True, "templates": get_template_repository().health(), "runtime": manager.stats(), "time": time.time()}


@app.post("/api/auth/register", dependencies=[rate_limit("auth")])
def register(body: Credentials, request: Request):
    if not settings.allow_register:
        raise HTTPException(403, "暂未开放注册")
    validate_credentials(body.username, body.password)
    if db.get_user_by_name(body.username):
        raise HTTPException(409, "用户名已存在")
    uid = db.create_user(body.username, hash_password(body.password), (body.display_name or body.username)[:32])
    db.audit(uid, "register", ip=request.client.host if request.client else "")
    return {"token": issue_token(uid, body.username), "user": db.get_user(uid)}


@app.post("/api/auth/login", dependencies=[rate_limit("auth")])
def login(body: Credentials, request: Request):
    u = db.get_user_by_name(body.username)
    ip = request.client.host if request.client else ""
    if u and u.get("locked_until") and u["locked_until"].timestamp() > time.time():
        raise HTTPException(423, "登录失败次数过多，账号已临时锁定，请稍后再试")
    if not u or not verify_password(body.password, u["password_hash"]) or u["status"] != 1:
        if u:
            db.record_login(u["id"], False)
        db.audit(u["id"] if u else None, "login_failed", target=body.username[:32], ip=ip)
        raise HTTPException(401, "用户名或密码错误")
    db.record_login(u["id"], True)
    db.audit(u["id"], "login", ip=ip)
    return {"token": issue_token(u["id"], u["username"]), "user": db.get_user(u["id"])}


@app.get("/api/auth/me")
def me(user=CurrentUser):
    from . import account
    return {**user, "avatar": account.get_avatar(user["id"])}


# ------------------------------------------------------------ 个人主页：资料 / 密码 / 额度 / 自有模型
class ProfileIn(BaseModel):
    display_name: Optional[str] = Field(None, max_length=32)
    avatar: Optional[str] = Field(None, max_length=450_000)
    clear_avatar: bool = False


class PasswordIn(BaseModel):
    old_password: str = Field(..., max_length=72)
    new_password: str = Field(..., max_length=72)


FORMATS = ("openai_chat", "openai_responses", "anthropic", "gemini")


class ModelIn(BaseModel):
    provider: str = Field(..., max_length=32)
    display_name: str = Field(..., min_length=1, max_length=64)
    api_format: str = Field(..., max_length=32)
    base_url: str = Field(..., min_length=8, max_length=500)
    model: str = Field(..., min_length=1, max_length=200)
    api_key: Optional[str] = Field(None, max_length=500)


def _check_model_in(body: ModelIn, require_key: bool):
    from urllib.parse import urlparse
    if body.api_format not in FORMATS:
        raise HTTPException(400, "不支持的 API 格式")
    u = urlparse(body.base_url.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise HTTPException(400, "Base URL 格式不正确（需以 http:// 或 https:// 开头）")
    if require_key and not (body.api_key or "").strip():
        raise HTTPException(400, "请填写 API Key")


@app.get("/api/account")
def account_overview(user=CurrentUser):
    from . import account
    from agent.runtime.providers import PRESETS
    models = account.list_models(user["id"])
    active = next((m for m in models if m["is_active"]), None)
    return {"user": {**user, "avatar": account.get_avatar(user["id"])}, "quota": account.get_quota(user["id"]),
            "models": models, "active_model_id": active["id"] if active else None, "presets": PRESETS,
            "formats": [{"id": "openai_chat", "name": "OpenAI Chat Completions"},
                        {"id": "openai_responses", "name": "OpenAI Responses API"},
                        {"id": "anthropic", "name": "Anthropic Messages"},
                        {"id": "gemini", "name": "Google Gemini"}]}


@app.patch("/api/account/profile")
def update_profile(body: ProfileIn, user=CurrentUser):
    from . import account
    if body.display_name is not None:
        if not body.display_name.strip():
            raise HTTPException(400, "昵称不能为空")
        account.update_display_name(user["id"], body.display_name.strip())
    try:
        if body.clear_avatar:
            account.set_avatar(user["id"], None)
        elif body.avatar:
            account.set_avatar(user["id"], body.avatar)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "user": {**db.get_user(user["id"]), "avatar": account.get_avatar(user["id"])}}


@app.post("/api/account/password", dependencies=[rate_limit("auth")])
def change_password(body: PasswordIn, request: Request, user=CurrentUser):
    from . import account
    if not verify_password(body.old_password, account.get_password_hash(user["id"])):
        raise HTTPException(400, "原密码不正确")
    validate_credentials(user["username"], body.new_password)
    if body.old_password == body.new_password:
        raise HTTPException(400, "新密码不能与原密码相同")
    account.update_password(user["id"], hash_password(body.new_password))
    db.audit(user["id"], "change_password", ip=request.client.host if request.client else "")
    return {"ok": True}


@app.post("/api/account/models")
def create_model(body: ModelIn, user=CurrentUser):
    from . import account
    _check_model_in(body, require_key=True)
    if len(account.list_models(user["id"])) >= 20:
        raise HTTPException(400, "最多保存 20 个模型配置")
    mid = account.save_model(user["id"], body.model_dump())
    return {"ok": True, "id": mid}


@app.patch("/api/account/models/{mid}")
def update_model(mid: int, body: ModelIn, user=CurrentUser):
    from . import account
    if not account.get_model(user["id"], mid):
        raise HTTPException(404, "模型配置不存在")
    _check_model_in(body, require_key=False)
    account.save_model(user["id"], body.model_dump(), mid)
    return {"ok": True}


@app.delete("/api/account/models/{mid}")
def remove_model(mid: int, user=CurrentUser):
    from . import account
    account.delete_model(user["id"], mid)
    return {"ok": True}


@app.post("/api/account/models/{mid}/activate")
def activate_model(mid: int, user=CurrentUser):
    from . import account
    if not account.get_model(user["id"], mid):
        raise HTTPException(404, "模型配置不存在")
    account.activate_model(user["id"], mid)
    return {"ok": True}


@app.post("/api/account/models/use-free")
def use_free(user=CurrentUser):
    from . import account
    account.activate_model(user["id"], None)
    return {"ok": True}


@app.post("/api/account/models/test", dependencies=[rate_limit("msg")])
def test_model(body: ModelIn, user=CurrentUser, model_id: Optional[int] = None):
    """连通性测试：可测试未保存的配置；编辑已保存配置时未填 Key 则使用已保存的 Key"""
    from . import account
    from agent.runtime.providers import Provider, test_connection
    _check_model_in(body, require_key=model_id is None)
    key = (body.api_key or "").strip()
    if not key and model_id is not None:
        saved = account.get_model(user["id"], model_id, with_key=True)
        if not saved:
            raise HTTPException(404, "模型配置不存在")
        key = saved["api_key"]
    res = test_connection(Provider(format=body.api_format, base_url=body.base_url.strip(), api_key=key,
                                   model=body.model.strip()))
    if model_id is not None:
        account.mark_test(user["id"], model_id, res["ok"])
    return res


# ------------------------------------------------------------ 会话
@app.get("/api/conversations")
def list_conversations(user=CurrentUser):
    return {"items": [_public_conv(c) for c in db.list_conversations(user["id"])]}


@app.post("/api/conversations")
def create_conversation(body: NewConversation, user=CurrentUser):
    conv = db.create_conversation(user["id"], body.title or "新的合同")
    return _public_conv(conv)


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str, user=CurrentUser):
    conv = _conv_or_404(user, cid, with_state=True)
    state = conv.get("state") or {}
    contract = db.latest_contract(cid)
    return {
        "conversation": _public_conv(conv),
        "messages": db.list_messages(cid),
        "events": db.list_events(cid),
        "contract": contract,
        "pipeline": {"current_step": state.get("current_step", 1), "step_done": state.get("step_done", []),
                     "degraded": state.get("degraded", []), "status": state.get("status", "idle")},
        "live_event_id": manager.channel(cid).seq,
    }


@app.patch("/api/conversations/{cid}")
def rename(cid: str, body: Rename, user=CurrentUser):
    _conv_or_404(user, cid)
    db.rename_conversation(user["id"], cid, body.title.strip())
    return {"ok": True}


@app.delete("/api/conversations/{cid}")
def delete(cid: str, user=CurrentUser):
    _conv_or_404(user, cid)
    manager.stop(cid, "deleted", wait=5)
    db.soft_delete_conversation(user["id"], cid)
    return {"ok": True}


@app.post("/api/conversations/{cid}/messages", dependencies=[rate_limit("msg")])
def send_message(cid: str, body: Message, user=CurrentUser):
    conv = _conv_or_404(user, cid, with_state=True)
    if db.message_exists(cid, body.client_msg_id):   # 幂等：网络重试 / 重复点击
        return {"ok": True, "duplicate": True}
    if conv["status"] in ("completed", "closed"):
        raise HTTPException(409, "会话已结束，不再接受输入。请新建任务。")
    state = conv.get("state") or {}
    manager.settle_waiting(cid)
    if manager.is_running(cid):
        raise HTTPException(409, "AI 正在处理中，请等待或先点击停止")
    text = sanitize_input(body.text)
    if not text:
        raise HTTPException(400, "消息不能为空")
    step = (state or {}).get("current_step", 1)
    mid = db.add_message(cid, "user", text, step=step, client_msg_id=body.client_msg_id)
    if mid is None:
        return {"ok": True, "duplicate": True}
    if conv["title"] == "新的合同" and step == 1:
        db.rename_conversation(user["id"], cid, text[:24] + ("…" if len(text) > 24 else ""))
    manager.emit(cid, "user.message", {"text": text, "message_id": mid}, step=step, agent="user")
    try:
        mode = manager.submit_input(user["id"], conv, text)
    except QuotaExhausted as e:
        raise HTTPException(402, str(e))
    except PermissionError as e:
        raise HTTPException(429 if "繁忙" in str(e) or "不能超过" in str(e) else 409, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "message_id": mid, "mode": mode}


@app.post("/api/conversations/{cid}/stop")
def stop(cid: str, user=CurrentUser):
    _conv_or_404(user, cid)
    stopped = manager.stop(cid, "user_stop")
    return {"ok": True, "stopped": stopped}


@app.post("/api/conversations/{cid}/resume")
def resume(cid: str, user=CurrentUser):
    conv = _conv_or_404(user, cid, with_state=True)
    if conv["status"] in ("completed", "closed"):
        raise HTTPException(409, "会话已结束")
    if manager.is_running(cid):
        return {"ok": True, "mode": "already_running"}
    state = conv.get("state") or {}
    first_input = None
    if not state:
        # 在第一个检查点之前就中断（如需求提取阶段额度耗尽 / 停止）：用最后一条用户消息从头重放
        users = [m for m in db.list_messages(cid) if m["role"] == "user"]
        if not users:
            raise HTTPException(409, "尚未开始，请先发送需求")
        first_input = users[-1]["content"]
    try:
        mode = manager.start(user["id"], conv, first_input)
    except QuotaExhausted as e:
        raise HTTPException(402, str(e))
    except PermissionError as e:
        raise HTTPException(429, str(e))
    return {"ok": True, "mode": mode}


@app.get("/api/conversations/{cid}/events")
async def events(cid: str, request: Request, user=CurrentUser, last_event_id: Optional[int] = None):
    _conv_or_404(user, cid)
    hdr = request.headers.get("last-event-id")
    last = int(hdr) if hdr and hdr.isdigit() else last_event_id
    ch = manager.channel(cid)
    sub = ch.subscribe(last)
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()
    # Agent 线程发布事件时线程安全地唤醒本协程：没有新事件时连接完全挂起，不轮询
    sub.bind(lambda: loop.call_soon_threadsafe(wake.set))

    async def gen():
        yield "retry: 3000\n\n"
        try:
            while True:
                try:
                    await asyncio.wait_for(wake.wait(), timeout=15)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
                    continue
                wake.clear()
                while True:
                    batch = sub.drain(200)
                    if not batch:
                        break
                    yield "".join(f"id: {ev['id']}\nevent: message\ndata: "
                                  f"{json.dumps(ev, ensure_ascii=False, default=str)}\n\n" for ev in batch)
        finally:
            ch.unsubscribe(sub)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/api/conversations/{cid}/export.docx")
def export_docx(cid: str, user=CurrentUser):
    conv = _conv_or_404(user, cid)
    latest = db.latest_contract(cid)
    if not latest:
        raise HTTPException(404, "暂无可导出的合同")
    data = contract_to_docx(latest["content"], conv["title"])
    from urllib.parse import quote
    name = quote(safe_filename(conv["title"]) + ".docx")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})
