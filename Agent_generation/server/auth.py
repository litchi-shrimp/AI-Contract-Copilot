"""认证：bcrypt 密码哈希 + JWT（HS256），登录失败锁定，统一依赖注入"""
import re
import time
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request

from . import db
from .config import settings

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{3,32}$")


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode())
    except Exception:
        return False


def validate_credentials(username: str, password: str):
    if not USERNAME_RE.match(username or ""):
        raise HTTPException(400, "用户名需为 3-32 位字母、数字、下划线或中文")
    if not password or len(password) < 8 or len(password) > 72:
        raise HTTPException(400, "密码长度需为 8-72 位")
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        raise HTTPException(400, "密码需同时包含字母和数字")


def issue_token(uid: int, username: str) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(uid), "name": username, "iat": now, "exp": now + settings.jwt_ttl_minutes * 60},
                      settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def _token_from(request: Request) -> Optional[str]:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    # EventSource 无法设置请求头，SSE 允许 query 参数传 token（仅用于 GET 事件流）
    return request.query_params.get("token")


def current_user(request: Request) -> dict:
    tok = _token_from(request)
    payload = decode_token(tok) if tok else None
    if not payload:
        raise HTTPException(401, "未登录或登录已过期")
    user = db.get_user(int(payload["sub"]))
    if not user or user["status"] != 1:
        raise HTTPException(401, "账号不可用")
    return user


CurrentUser = Depends(current_user)
