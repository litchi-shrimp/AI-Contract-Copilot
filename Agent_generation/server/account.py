"""用户账户：免费额度、个人资料、自有模型 API 配置

免费额度（平台承担费用，使用平台 deepseek-flash）：
- FREE_RUNS（默认 2）：完整合同生成次数，流程走完（Step 8 确认）才计 1 次
- FREE_TOKENS（默认 1,800,000）：平台 Key 的累计 Token 上限
  估算依据：6 个评测用例真实运行，单次完整生成 39 万 ~ 93 万 tokens（均值 ≈ 60 万，含输入上下文）→ 3 次 ≈ 180 万
- 任一额度用尽 → 必须配置自有 API 才能继续；已配置并启用自有 API 时，不消耗免费额度

API Key 加密：Fernet（AES-128-CBC + HMAC），密钥来自环境变量 SECRET_KEY 或本地 .secret_key 文件；接口只返回脱敏提示
"""
import base64
import hashlib
import os
import re
from pathlib import Path
from typing import Optional

from sqlalchemy import text

from . import db
from .config import ROOT, settings

FREE_RUNS = int(os.getenv("FREE_RUNS", "2"))
FREE_TOKENS = int(os.getenv("FREE_TOKENS", "1800000"))
TOKENS_PER_RUN_ESTIMATE = 600_000


# ------------------------------------------------------------ 加密
def _fernet():
    from cryptography.fernet import Fernet
    secret = os.getenv("SECRET_KEY")
    if not secret:
        f = ROOT / ".secret_key"
        if not f.exists():
            f.write_text(base64.urlsafe_b64encode(os.urandom(32)).decode(), encoding="utf-8")
        secret = f.read_text(encoding="utf-8").strip()
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(s: str) -> str:
    return _fernet().encrypt(s.encode()).decode()


def decrypt(s: str) -> str:
    return _fernet().decrypt(s.encode()).decode()


def key_hint(k: str) -> str:
    k = k or ""
    return (k[:3] + "…" + k[-4:]) if len(k) > 10 else "…" + k[-2:]


# ------------------------------------------------------------ 额度
def get_quota(uid: int) -> dict:
    with db.engine().begin() as c:
        c.execute(text("INSERT IGNORE INTO user_quota (user_id) VALUES (:u)"), {"u": uid})
        r = c.execute(text("SELECT free_runs_used, free_tokens_used FROM user_quota WHERE user_id=:u"), {"u": uid}).first()
    runs, tokens = int(r[0]), int(r[1])
    return {"free_runs_total": FREE_RUNS, "free_runs_used": runs, "free_runs_left": max(0, FREE_RUNS - runs),
            "free_tokens_total": FREE_TOKENS, "free_tokens_used": tokens, "free_tokens_left": max(0, FREE_TOKENS - tokens),
            "tokens_per_run_estimate": TOKENS_PER_RUN_ESTIMATE,
            "exhausted": runs >= FREE_RUNS or tokens >= FREE_TOKENS}


def charge(uid: int, cid: str, epoch: int, billing: str, model: str, tokens: int, calls: int, completed: bool):
    """运行结束（完成/停止/失败）时记账；仅 free 计费才扣免费额度"""
    with db.engine().begin() as c:
        c.execute(text("INSERT INTO usage_log (user_id, conversation_id, run_epoch, billing, model, tokens, llm_calls, completed) "
                       "VALUES (:u,:c,:e,:b,:m,:t,:n,:d)"),
                  {"u": uid, "c": cid, "e": epoch, "b": billing, "m": model[:200], "t": int(tokens), "n": int(calls),
                   "d": 1 if completed else 0})
        if billing == "free":
            c.execute(text("INSERT IGNORE INTO user_quota (user_id) VALUES (:u)"), {"u": uid})
            c.execute(text("UPDATE user_quota SET free_tokens_used=free_tokens_used+:t, "
                           "free_runs_used=free_runs_used+:r WHERE user_id=:u"),
                      {"t": int(tokens), "r": 1 if completed else 0, "u": uid})


# ------------------------------------------------------------ 资料
AVATAR_RE = re.compile(r"^data:image/(png|jpeg|jpg|webp|gif);base64,[A-Za-z0-9+/=]+$")
MAX_AVATAR_BYTES = 300 * 1024


def get_avatar(uid: int) -> Optional[str]:
    with db.engine().connect() as c:
        r = c.execute(text("SELECT avatar FROM user_profile WHERE user_id=:u"), {"u": uid}).first()
    return r[0] if r else None


def set_avatar(uid: int, data_url: Optional[str]):
    if data_url:
        if not AVATAR_RE.match(data_url):
            raise ValueError("头像格式不支持（仅 png/jpeg/webp/gif）")
        if len(base64.b64decode(data_url.split(",", 1)[1])) > MAX_AVATAR_BYTES:
            raise ValueError("头像不能超过 300KB")
    with db.engine().begin() as c:
        c.execute(text("INSERT INTO user_profile (user_id, avatar) VALUES (:u, :a) ON DUPLICATE KEY UPDATE avatar=:a"),
                  {"u": uid, "a": data_url})


def update_display_name(uid: int, name: str):
    with db.engine().begin() as c:
        c.execute(text("UPDATE users SET display_name=:n WHERE id=:u"), {"n": name[:32], "u": uid})


def update_password(uid: int, new_hash: str):
    with db.engine().begin() as c:
        c.execute(text("UPDATE users SET password_hash=:p WHERE id=:u"), {"p": new_hash, "u": uid})


def get_password_hash(uid: int) -> str:
    with db.engine().connect() as c:
        return c.execute(text("SELECT password_hash FROM users WHERE id=:u"), {"u": uid}).scalar()


# ------------------------------------------------------------ 自有模型
COLS = "id, provider, display_name, api_format, base_url, model, api_key_hint, is_active, last_test_ok, last_test_at, created_at"


def list_models(uid: int) -> list:
    with db.engine().connect() as c:
        rows = c.execute(text(f"SELECT {COLS} FROM user_models WHERE user_id=:u ORDER BY id"), {"u": uid}).mappings().all()
    return [dict(r) for r in rows]


def get_model(uid: int, mid: int, with_key: bool = False) -> Optional[dict]:
    cols = COLS + (", api_key_enc" if with_key else "")
    with db.engine().connect() as c:
        r = c.execute(text(f"SELECT {cols} FROM user_models WHERE id=:i AND user_id=:u"), {"i": mid, "u": uid}).mappings().first()
    if not r:
        return None
    d = dict(r)
    if with_key:
        d["api_key"] = decrypt(d.pop("api_key_enc"))
    return d


def save_model(uid: int, data: dict, mid: Optional[int] = None) -> int:
    params = {"u": uid, "p": data["provider"], "n": data["display_name"][:64], "f": data["api_format"],
              "b": data["base_url"][:500], "m": data["model"][:200]}
    with db.engine().begin() as c:
        if mid is None:
            params.update(k=encrypt(data["api_key"]), h=key_hint(data["api_key"]))
            r = c.execute(text("INSERT INTO user_models (user_id, provider, display_name, api_format, base_url, model, "
                               "api_key_enc, api_key_hint) VALUES (:u,:p,:n,:f,:b,:m,:k,:h)"), params)
            mid = int(r.lastrowid)
            has = c.execute(text("SELECT COUNT(*) FROM user_models WHERE user_id=:u AND is_active=1"), {"u": uid}).scalar()
            if not has:
                c.execute(text("UPDATE user_models SET is_active=1 WHERE id=:i"), {"i": mid})
        else:
            sets = "provider=:p, display_name=:n, api_format=:f, base_url=:b, model=:m, last_test_ok=NULL"
            if data.get("api_key"):
                sets += ", api_key_enc=:k, api_key_hint=:h"
                params.update(k=encrypt(data["api_key"]), h=key_hint(data["api_key"]))
            params["i"] = mid
            c.execute(text(f"UPDATE user_models SET {sets} WHERE id=:i AND user_id=:u"), params)
    return mid


def delete_model(uid: int, mid: int):
    with db.engine().begin() as c:
        c.execute(text("DELETE FROM user_models WHERE id=:i AND user_id=:u"), {"i": mid, "u": uid})


def activate_model(uid: int, mid: Optional[int]):
    """mid=None → 切回平台免费模型"""
    with db.engine().begin() as c:
        c.execute(text("UPDATE user_models SET is_active=0 WHERE user_id=:u"), {"u": uid})
        if mid is not None:
            c.execute(text("UPDATE user_models SET is_active=1 WHERE id=:i AND user_id=:u"), {"i": mid, "u": uid})


def mark_test(uid: int, mid: int, ok: bool):
    with db.engine().begin() as c:
        c.execute(text("UPDATE user_models SET last_test_ok=:o, last_test_at=NOW() WHERE id=:i AND user_id=:u"),
                  {"o": 1 if ok else 0, "i": mid, "u": uid})


def active_provider(uid: int):
    """返回用户启用的自有模型（Provider）或 None（使用平台免费额度）"""
    from agent.runtime.providers import Provider
    with db.engine().connect() as c:
        r = c.execute(text("SELECT id FROM user_models WHERE user_id=:u AND is_active=1 LIMIT 1"), {"u": uid}).first()
    if not r:
        return None
    m = get_model(uid, int(r[0]), with_key=True)
    return Provider(format=m["api_format"], base_url=m["base_url"], api_key=m["api_key"], model=m["model"],
                    label=m["display_name"])
