"""初始化数据库（建库 + 建表，幂等）

    python -m db.init_db                    # 使用环境变量 DATABASE_URL / MYSQL_*
    python -m db.init_db --url mysql+pymysql://root:123456@127.0.0.1:3306/contract_copilot?charset=utf8mb4
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.repository.db import database_url, ensure_database, get_engine  # noqa: E402

SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def split_sql(text: str):
    text = re.sub(r"--[^\n]*", "", text)
    return [s.strip() for s in text.split(";") if s.strip()]


def init_db(url: str):
    from sqlalchemy import text
    ensure_database(url)
    eng = get_engine(url)
    with eng.begin() as conn:
        for stmt in split_sql(SCHEMA.read_text(encoding="utf-8")):
            conn.execute(text(stmt))
        for mig in sorted((SCHEMA.parent / "migrations").glob("*.sql")):
            for stmt in split_sql(mig.read_text(encoding="utf-8")):
                conn.execute(text(stmt))
    eng.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None)
    a = ap.parse_args()
    u = a.url or database_url()
    if not u:
        sys.exit("请设置 DATABASE_URL 或 MYSQL_HOST/MYSQL_PASSWORD 等环境变量，或使用 --url")
    init_db(u)
    print("数据库初始化完成")
