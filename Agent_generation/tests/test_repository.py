"""模板库 Repository：文件后端 / MySQL 后端一致性 / 自动回退"""
import os
from pathlib import Path

import pytest

from agent.repository.template_repo import FileTemplateRepository, get_template_repository, reset_template_repository

LIB = Path(__file__).resolve().parent.parent.parent / "template_library"
FULL_LIB = Path(r"D:\明鉴智律公司\ContractGeneration\contract_generation\template_library")
MYSQL_URL = os.getenv("TEST_DATABASE_URL", "mysql+pymysql://root:123456@127.0.0.1:3306/contract_copilot?charset=utf8mb4")


def _mysql_available():
    try:
        from agent.repository.db import get_engine
        from sqlalchemy import text
        eng = get_engine(MYSQL_URL)
        with eng.connect() as c:
            return int(c.execute(text("SELECT COUNT(*) FROM contract_template")).scalar()) > 0
    except Exception:
        return False


def test_file_repo_reads_sample_library():
    repo = FileTemplateRepository(LIB)
    assert repo.classify_tree()
    assert len(repo.list_all()) >= 3
    assert all("_file" in t for t in repo.list_by_prefix("Accept"))


def test_auto_falls_back_to_file_when_mysql_unreachable(monkeypatch):
    monkeypatch.setenv("TEMPLATE_BACKEND", "auto")
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://root:x@127.0.0.1:1/none")
    from agent.repository import db
    db.reset_engine()
    reset_template_repository(None)
    try:
        assert get_template_repository().backend == "file"
    finally:
        db.reset_engine()
        reset_template_repository(None)


@pytest.mark.skipif(not _mysql_available(), reason="本地 MySQL 模板库不可用")
def test_mysql_matches_file_library():
    from agent.repository.db import get_engine
    from agent.repository.mysql_template_repo import MySQLTemplateRepository
    m = MySQLTemplateRepository(get_engine(MYSQL_URL))
    f = FileTemplateRepository(FULL_LIB if FULL_LIB.exists() else LIB)
    assert m.classify_tree() == f.classify_tree()
    for prefix in ("Real_Estate", "Tech", "Sell"):
        a = sorted(t["template_name"] for t in m.list_by_prefix(prefix))
        b = sorted(t["template_name"] for t in f.list_by_prefix(prefix))
        assert a == b
    tpl = m.list_by_prefix("Lease")[0]
    assert isinstance(tpl["standard_template_content"], dict) and "正文章节" in tpl["standard_template_content"]
