"""MySQL 模板库 Repository

表结构（见 db/schema.sql）：
- contract_category(id, path, name, category_id, parent_path, level)
- contract_template(id, template_id, template_key, template_name, contract_type, party_type, scene, complexity,
                    region, template_version, source_file, abstract, content_json, placeholder_json, created_at)
"""
import json
import threading
import time
from typing import Dict, List, Optional

from .db import get_engine
from .template_repo import TemplateRepository


class MySQLTemplateRepository(TemplateRepository):
    backend = "mysql"

    def __init__(self, engine, ttl: float = 300):
        self.engine = engine
        self.ttl = ttl
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> Optional["MySQLTemplateRepository"]:
        eng = get_engine()
        return cls(eng) if eng is not None else None

    def _cached(self, key, loader):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < self.ttl:
                return hit[1]
        val = loader()
        with self._lock:
            self._cache[key] = (time.time(), val)
        return val

    @staticmethod
    def _row_to_tpl(row) -> dict:
        m = row._mapping
        tpl = {
            "template_id": m["template_id"], "template_name": m["template_name"],
            "contract_type": m["contract_type"], "template_version": m["template_version"],
            "create_time": str(m["created_at"]) if m["created_at"] else "", "source_file": m["source_file"],
            "party_type": m["party_type"], "scene": m["scene"], "complexity": m["complexity"],
            "region": m["region"], "abstract": m["abstract"] or "",
            "standard_template_content": json.loads(m["content_json"] or "{}"),
            "placeholder_list": json.loads(m["placeholder_json"] or "[]"),
            "_file": f"mysql://contract_template/{m['id']}", "_key": str(m["template_key"]),
        }
        return tpl

    def count(self) -> int:
        from sqlalchemy import text
        with self.engine.connect() as conn:
            return int(conn.execute(text("SELECT COUNT(*) FROM contract_template")).scalar() or 0)

    def classify_tree(self) -> dict:
        from sqlalchemy import text

        def _load():
            with self.engine.connect() as conn:
                rows = conn.execute(text("SELECT tree_json FROM contract_category_tree ORDER BY id DESC LIMIT 1")).fetchall()
            return json.loads(rows[0][0]) if rows else {}
        return self._cached("classify", _load)

    def list_all(self) -> List[dict]:
        from sqlalchemy import text

        def _load():
            with self.engine.connect() as conn:
                rows = conn.execute(text("SELECT * FROM contract_template ORDER BY template_id, template_key")).fetchall()
            return [self._row_to_tpl(r) for r in rows]
        return [dict(x) for x in self._cached("all", _load)]

    def list_by_prefix(self, prefix: str) -> List[dict]:
        from sqlalchemy import text
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM contract_template WHERE template_id = :p OR template_id LIKE :lp "
                                     "ORDER BY template_id, template_key"),
                                {"p": prefix, "lp": prefix.replace("_", r"\_") + r"\_%"}).fetchall()
        return [self._row_to_tpl(r) for r in rows]

    def enum_values(self, fields=("party_type", "region", "complexity", "scene")) -> Dict[str, List[str]]:
        from sqlalchemy import text
        out = {}
        with self.engine.connect() as conn:
            for f in fields:
                rows = conn.execute(text(f"SELECT DISTINCT {f} FROM contract_template WHERE {f} <> '' ORDER BY {f}"))
                out[f] = [r[0] for r in rows]
        return out

    def add_template(self, template_id: str, record: dict) -> str:
        from sqlalchemy import text
        with self.engine.begin() as conn:
            key = conn.execute(text("SELECT COALESCE(MAX(template_key), -1) + 1 FROM contract_template "
                                    "WHERE template_id=:t FOR UPDATE"), {"t": template_id}).scalar()
            conn.execute(text(
                "INSERT INTO contract_template (template_id, template_key, template_name, contract_type, party_type, "
                "scene, complexity, region, template_version, source_file, abstract, content_json, placeholder_json, "
                "is_recommended) VALUES (:template_id, :template_key, :template_name, :contract_type, :party_type, "
                ":scene, :complexity, :region, :template_version, :source_file, :abstract, :content_json, "
                ":placeholder_json, :is_recommended)"), template_params(template_id, int(key), record))
        with self._lock:
            self._cache.clear()
        return str(key)

    def health(self) -> dict:
        from sqlalchemy import text
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return {"backend": self.backend, "ok": True}
        except Exception as e:  # noqa: BLE001
            return {"backend": self.backend, "ok": False, "error": str(e)[:200]}


def template_params(template_id: str, key: int, tpl: dict) -> dict:
    return {
        "template_id": template_id, "template_key": key,
        "template_name": str(tpl.get("template_name", ""))[:500],
        "contract_type": str(tpl.get("contract_type", ""))[:255],
        "party_type": str(tpl.get("party_type", ""))[:64], "scene": str(tpl.get("scene", ""))[:128],
        "complexity": str(tpl.get("complexity", ""))[:32], "region": str(tpl.get("region", ""))[:64],
        "template_version": str(tpl.get("template_version", "v1.0"))[:32],
        "source_file": str(tpl.get("source_file", ""))[:1000], "abstract": tpl.get("abstract", "") or "",
        "content_json": json.dumps(tpl.get("standard_template_content", {}), ensure_ascii=False),
        "placeholder_json": json.dumps(tpl.get("placeholder_list", []), ensure_ascii=False),
        "is_recommended": int(tpl.get("is_recommended", 0) or 0),
    }
