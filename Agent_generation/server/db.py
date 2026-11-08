"""数据访问层（SQLAlchemy Core，所有写操作在事务中完成）

并发控制：
- conversations.state_version：乐观锁，检查点写入必须带上读到的版本，版本不符即拒绝（防止旧状态覆盖新状态）
- conversations.run_epoch：每次启动运行 +1；运行线程写检查点时校验 epoch，旧运行（已被停止的残留线程）写入被拒绝
- messages.client_msg_id：前端生成的幂等键，重复提交（网络重试 / 双击）只落一条
"""
import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from agent.repository.db import ensure_database, get_engine as _get_engine
from .config import settings

_engine = None
_lock = threading.Lock()


def engine():
    global _engine
    with _lock:
        if _engine is None:
            _engine = _get_engine(settings.database_url)
        return _engine


def init_schema():
    from db.init_db import init_db
    init_db(settings.database_url)


class StaleWrite(Exception):
    """乐观锁冲突 / 旧运行写入"""


# ============================================================ users
def create_user(username: str, password_hash: str, display_name: str) -> int:
    with engine().begin() as c:
        r = c.execute(text("INSERT INTO users (username, password_hash, display_name) VALUES (:u, :p, :d)"),
                      {"u": username, "p": password_hash, "d": display_name})
        return int(r.lastrowid)


def get_user_by_name(username: str) -> Optional[dict]:
    with engine().connect() as c:
        r = c.execute(text("SELECT * FROM users WHERE username=:u"), {"u": username}).mappings().first()
        return dict(r) if r else None


def get_user(uid: int) -> Optional[dict]:
    with engine().connect() as c:
        r = c.execute(text("SELECT id, username, display_name, status, created_at FROM users WHERE id=:i"),
                      {"i": uid}).mappings().first()
        return dict(r) if r else None


def record_login(uid: int, ok: bool):
    with engine().begin() as c:
        if ok:
            c.execute(text("UPDATE users SET failed_logins=0, locked_until=NULL, last_login_at=NOW() WHERE id=:i"),
                      {"i": uid})
        else:
            # 注意：MySQL 单表 UPDATE 的 SET 从左到右求值，先计算 locked_until 再自增计数
            c.execute(text("UPDATE users SET locked_until=IF(failed_logins+1>=:m, "
                           "DATE_ADD(NOW(), INTERVAL :l MINUTE), locked_until), failed_logins=failed_logins+1 "
                           "WHERE id=:i"),
                      {"i": uid, "m": settings.login_max_failures, "l": settings.login_lock_minutes})


def audit(user_id: Optional[int], action: str, target: str = "", ip: str = "", detail: str = ""):
    try:
        with engine().begin() as c:
            c.execute(text("INSERT INTO audit_log (user_id, action, target, ip, detail) VALUES (:u,:a,:t,:i,:d)"),
                      {"u": user_id, "a": action[:64], "t": target[:64], "i": ip[:64], "d": detail[:1000]})
    except Exception:
        pass


# ============================================================ conversations
def create_conversation(uid: int, title: str = "新的合同") -> dict:
    cid = uuid.uuid4().hex[:20]
    with engine().begin() as c:
        c.execute(text("INSERT INTO conversations (id, user_id, title) VALUES (:i, :u, :t)"),
                  {"i": cid, "u": uid, "t": title[:200]})
    return get_conversation(uid, cid)


def get_conversation(uid: int, cid: str, with_state: bool = False) -> Optional[dict]:
    cols = "id, user_id, title, status, current_step, state_version, run_epoch, last_error, created_at, updated_at"
    if with_state:
        cols += ", state_json"
    with engine().connect() as c:
        r = c.execute(text(f"SELECT {cols} FROM conversations WHERE id=:i AND user_id=:u AND deleted=0"),
                      {"i": cid, "u": uid}).mappings().first()
    if not r:
        return None
    d = dict(r)
    if with_state:
        d["state"] = json.loads(d.pop("state_json") or "null")
    return d


def list_conversations(uid: int, limit: int = 100) -> List[dict]:
    with engine().connect() as c:
        rows = c.execute(text("SELECT id, title, status, current_step, created_at, updated_at FROM conversations "
                              "WHERE user_id=:u AND deleted=0 ORDER BY updated_at DESC LIMIT :l"),
                         {"u": uid, "l": limit}).mappings().all()
    return [dict(r) for r in rows]


def rename_conversation(uid: int, cid: str, title: str) -> bool:
    with engine().begin() as c:
        r = c.execute(text("UPDATE conversations SET title=:t WHERE id=:i AND user_id=:u AND deleted=0"),
                      {"t": title[:200], "i": cid, "u": uid})
        return r.rowcount > 0


def soft_delete_conversation(uid: int, cid: str) -> bool:
    with engine().begin() as c:
        r = c.execute(text("UPDATE conversations SET deleted=1 WHERE id=:i AND user_id=:u"), {"i": cid, "u": uid})
        return r.rowcount > 0


def bump_epoch(cid: str) -> int:
    """开始一次新运行：epoch+1（之后旧运行的任何写入都会被拒绝）"""
    with engine().begin() as c:
        c.execute(text("UPDATE conversations SET run_epoch=run_epoch+1 WHERE id=:i"), {"i": cid})
        return int(c.execute(text("SELECT run_epoch FROM conversations WHERE id=:i"), {"i": cid}).scalar())


def save_checkpoint(cid: str, epoch: int, state: dict, status: str, current_step: int,
                    error: Optional[str] = None):
    """原子写检查点：同一事务内校验 epoch，旧运行写入抛 StaleWrite"""
    with engine().begin() as c:
        r = c.execute(text("UPDATE conversations SET state_json=:s, status=:st, current_step=:cs, last_error=:e, "
                           "state_version=state_version+1 WHERE id=:i AND run_epoch=:ep"),
                      {"s": json.dumps(state, ensure_ascii=False), "st": status, "cs": current_step,
                       "e": (error or None) and error[:1000], "i": cid, "ep": epoch})
        if r.rowcount == 0:
            raise StaleWrite(f"conversation {cid} epoch {epoch} 已过期")


def set_status(cid: str, status: str, epoch: Optional[int] = None, error: Optional[str] = None) -> bool:
    q = "UPDATE conversations SET status=:s, last_error=:e WHERE id=:i"
    params = {"s": status, "e": (error or None) and error[:1000], "i": cid}
    if epoch is not None:
        q += " AND run_epoch=:ep"
        params["ep"] = epoch
    with engine().begin() as c:
        return c.execute(text(q), params).rowcount > 0


def recover_interrupted_runs() -> int:
    """服务启动时：上次异常退出遗留的 running 状态 → paused（用户可点击继续，从检查点恢复）"""
    with engine().begin() as c:
        r = c.execute(text("UPDATE conversations SET status='paused', last_error='服务重启，运行已中断，可继续' "
                           "WHERE status='running'"))
        return r.rowcount


def close_idle(hours: float) -> int:
    with engine().begin() as c:
        r = c.execute(text("UPDATE conversations SET status='closed' WHERE status IN ('waiting_input','paused','idle') "
                           "AND updated_at < DATE_SUB(NOW(), INTERVAL :h HOUR) AND deleted=0"), {"h": int(hours)})
        return r.rowcount


# ============================================================ messages
def add_message(cid: str, role: str, content: str, step: Optional[int] = None, meta: Optional[dict] = None,
                client_msg_id: Optional[str] = None) -> Optional[int]:
    """返回新消息 id；client_msg_id 重复时返回 None（幂等）"""
    with engine().begin() as c:
        try:
            r = c.execute(text("INSERT INTO messages (conversation_id, role, step, content, meta_json, client_msg_id) "
                               "VALUES (:c, :r, :s, :t, :m, :k)"),
                          {"c": cid, "r": role, "s": step, "t": content,
                           "m": json.dumps(meta, ensure_ascii=False) if meta else None, "k": client_msg_id})
        except Exception as e:  # noqa: BLE001
            if "Duplicate" in str(e) and client_msg_id:
                return None
            raise
        c.execute(text("UPDATE conversations SET updated_at=NOW() WHERE id=:c"), {"c": cid})
        return int(r.lastrowid)


def message_exists(cid: str, client_msg_id: Optional[str]) -> bool:
    if not client_msg_id:
        return False
    with engine().connect() as c:
        return c.execute(text("SELECT 1 FROM messages WHERE conversation_id=:c AND client_msg_id=:k"),
                         {"c": cid, "k": client_msg_id}).first() is not None


def list_messages(cid: str) -> List[dict]:
    with engine().connect() as c:
        rows = c.execute(text("SELECT id, role, step, content, meta_json, created_at FROM messages "
                              "WHERE conversation_id=:c ORDER BY id"), {"c": cid}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d.pop("meta_json") or "null")
        out.append(d)
    return out


# ============================================================ events / versions
def add_events(cid: str, epoch: int, events: List[dict]):
    if not events:
        return
    with engine().begin() as c:
        c.execute(text("INSERT INTO conversation_events (conversation_id, run_epoch, event_type, step, agent, data_json) "
                       "VALUES (:c, :e, :t, :s, :a, :d)"),
                  [{"c": cid, "e": epoch, "t": ev["event_type"][:48], "s": ev.get("step"), "a": (ev.get("agent") or "")[:64],
                    "d": json.dumps(ev.get("data") or {}, ensure_ascii=False)} for ev in events])


def list_events(cid: str, after_id: int = 0, limit: int = 5000) -> List[dict]:
    with engine().connect() as c:
        rows = c.execute(text("SELECT id, run_epoch, event_type, step, agent, data_json, created_at FROM conversation_events "
                              "WHERE conversation_id=:c AND id>:a ORDER BY id LIMIT :l"),
                         {"c": cid, "a": after_id, "l": limit}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["data"] = json.loads(d.pop("data_json") or "{}")
        out.append(d)
    return out


def add_contract_version(cid: str, step: int, kind: str, content: dict):
    with engine().begin() as c:
        c.execute(text("INSERT INTO contract_versions (conversation_id, step, kind, content_json) VALUES (:c,:s,:k,:j)"),
                  {"c": cid, "s": step, "k": kind, "j": json.dumps(content, ensure_ascii=False)})


def latest_contract(cid: str) -> Optional[dict]:
    with engine().connect() as c:
        r = c.execute(text("SELECT step, kind, content_json, created_at FROM contract_versions WHERE conversation_id=:c "
                           "ORDER BY id DESC LIMIT 1"), {"c": cid}).mappings().first()
    if not r:
        return None
    return {"step": r["step"], "kind": r["kind"], "content": json.loads(r["content_json"]),
            "created_at": r["created_at"]}


def count_active_runs(uid: Optional[int] = None) -> int:
    q = "SELECT COUNT(*) FROM conversations WHERE status='running' AND deleted=0"
    params: Dict[str, Any] = {}
    if uid is not None:
        q += " AND user_id=:u"
        params["u"] = uid
    with engine().connect() as c:
        return int(c.execute(text(q), params).scalar())
