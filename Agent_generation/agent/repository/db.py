"""数据库连接（SQLAlchemy Core + PyMySQL）

配置：DATABASE_URL（优先），或 MYSQL_HOST/MYSQL_PORT/MYSQL_USER/MYSQL_PASSWORD/MYSQL_DB
连接池：pool_pre_ping（自动剔除失效连接）+ pool_recycle（规避 MySQL wait_timeout）
所有写操作使用事务（engine.begin()），保证原子性。
"""
import os
import threading
from typing import Optional
from urllib.parse import quote_plus

_engine = None
_lock = threading.Lock()


def database_url() -> Optional[str]:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    host = os.getenv("MYSQL_HOST")
    if not host:
        return None
    user = os.getenv("MYSQL_USER", "root")
    pwd = quote_plus(os.getenv("MYSQL_PASSWORD", ""))
    port = os.getenv("MYSQL_PORT", "3306")
    db = os.getenv("MYSQL_DB", "contract_copilot")
    return f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{db}?charset=utf8mb4"


def get_engine(url: Optional[str] = None):
    global _engine
    from sqlalchemy import create_engine
    if url is not None:
        return create_engine(url, pool_pre_ping=True, pool_recycle=1800, pool_size=10, max_overflow=20,
                             future=True)
    with _lock:
        if _engine is None:
            u = database_url()
            if not u:
                return None
            _engine = create_engine(u, pool_pre_ping=True, pool_recycle=1800, pool_size=10, max_overflow=20,
                                    future=True)
        return _engine


def reset_engine():
    global _engine
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None


def ensure_database(url: str):
    """若目标库不存在则创建（utf8mb4）。直接用 PyMySQL 连接服务器（不指定库）"""
    import pymysql
    from sqlalchemy.engine import make_url
    u = make_url(url)
    conn = pymysql.connect(host=u.host or "127.0.0.1", port=int(u.port or 3306), user=u.username or "root",
                           password=u.password or "", charset="utf8mb4", connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{u.database}` DEFAULT CHARACTER SET utf8mb4 "
                        f"COLLATE utf8mb4_unicode_ci")
        conn.commit()
    finally:
        conn.close()