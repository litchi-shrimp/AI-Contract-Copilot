#!/usr/bin/env python3
"""
SSE Server — FastAPI 应用入口

提供以下端点：
  GET  /api/contract/generate/sse   — SSE 长连接（流式推送 Agent 事件）
  POST /api/contract/start          — 启动合同生成流程
  POST /api/contract/user-input     — 向交互式 Agent 发送用户输入
  GET  /api/contract/status/{sid}   — 查询 Session 状态
  GET  /api/contract/sessions       — 列出所有活跃 Session
"""
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

# 确保项目根目录在 sys.path 中
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from .session_manager import get_session_manager
from .orchestrator import run_workflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("sse_server")

app = FastAPI(title="合同生成系统 SSE API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== SSE 事件格式化 ====================

def _format_sse_event(event: dict) -> str:
    """将 EventBus 事件格式化为 SSE 协议文本

    注意：不使用 event: 字段（避免浏览器 EventSource 命名事件分派问题），
    所有事件类型通过 data 中的 event_type 字段传递，前端统一通过 onmessage 接收。
    """
    local_id = event.get("event_id", f"evt_{int(time.time())}")
    return f"id: {local_id}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def _format_ping() -> str:
    data = json.dumps({
        "event_type": "session.ping",
        "event_id": f"ping_{int(time.time())}",
        "timestamp": time.time(),
        "data": {"message": "keepalive"},
    }, ensure_ascii=False)
    return f"id: ping_{int(time.time())}\ndata: {data}\n\n"


# ==================== SSE 端点 ====================

@app.get("/api/contract/generate/sse")
async def sse_endpoint(
    request: Request,
    session_id: str = Query(..., description="Session ID"),
    last_event_id: Optional[str] = Query(None, alias="lastEventId", description="断线重连的最后事件 ID"),
):
    sm = get_session_manager()
    state = sm.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} 不存在或已过期")

    eb = state.event_bus
    state.sse_connections += 1
    logger.info(f"SSE 连接建立: session={session_id}, reconnect={bool(last_event_id)}")

    async def event_generator():
        try:
            if last_event_id:
                cached = eb.buffer.get_since(last_event_id)
                for evt in cached:
                    if await request.is_disconnected():
                        return
                    yield _format_sse_event(evt)

            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(eb.queue.get(), timeout=30)
                    yield _format_sse_event(event)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield _format_ping()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"SSE 事件循环异常: {e}")
        finally:
            state.sse_connections -= 1
            logger.info(f"SSE 连接断开: session={session_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== 管理端点 ====================

@app.post("/api/contract/start")
async def start_contract_generation(request: Request):
    sm = get_session_manager()
    body = await request.json() if request.headers.get("content-type") else {}
    user_need = body.get("user_need")
    timeout_seconds = body.get("timeout_seconds", 1800)

    session_id = sm.create_session(timeout_seconds=timeout_seconds)
    state = sm.get_session(session_id)

    state.workflow_task = asyncio.create_task(
        run_workflow(session_id, user_need_input=user_need)
    )

    logger.info(f"启动合同生成: session={session_id}")
    return {
        "session_id": session_id,
        "sse_url": f"/api/contract/generate/sse?session_id={session_id}",
        "status": "started",
    }


@app.post("/api/contract/user-input")
async def user_input(request: Request):
    body = await request.json()
    session_id = body.get("session_id", "")
    text = body.get("text", "")

    if not session_id or not text:
        raise HTTPException(status_code=400, detail="缺少 session_id 或 text")

    sm = get_session_manager()
    state = sm.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} 不存在或已过期")

    state.event_bus.set_user_input(text)
    await state.event_bus.emit_user_input(text, step=state.current_step)

    logger.info(f"用户输入: session={session_id}, text={text[:50]}")
    return {"status": "ok", "session_id": session_id}


@app.get("/api/contract/status/{session_id}")
async def get_session_status(session_id: str):
    sm = get_session_manager()
    state = sm.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} 不存在或已过期")
    return state.to_dict()


@app.get("/api/contract/sessions")
async def list_sessions():
    sm = get_session_manager()
    return {"sessions": sm.list_sessions()}


@app.delete("/api/contract/session/{session_id}")
async def destroy_session(session_id: str):
    sm = get_session_manager()
    await sm.destroy_session(session_id)
    return {"status": "destroyed", "session_id": session_id}


# ==================== 启动事件 ====================

@app.on_event("startup")
async def startup():
    sm = get_session_manager()
    await sm.start()
    logger.info("SSE Server 启动完成")


@app.on_event("shutdown")
async def shutdown():
    sm = get_session_manager()
    await sm.stop()
    logger.info("SSE Server 已关闭")


# ==================== 直接运行 ====================

def main():
    uvicorn.run(
        "sse_server.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )

