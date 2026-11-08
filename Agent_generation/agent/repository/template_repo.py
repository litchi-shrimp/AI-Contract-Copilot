"""模板库 Repository

- FileTemplateRepository：读取 template_library/*.json（开源示例 / 无数据库环境）
- MySQLTemplateRepository：读取 MySQL（完整模板库，见 db/）
- get_template_repository()：按配置选择后端；MySQL 不可用时自动回退文件后端并告警（降级而非崩溃）

模板记录统一为 dict（与原 JSON 中单个模板结构一致），附加 _file/_key 字段（兼容旧代码）。
带进程内缓存（TTL），模板库是读多写少数据。
"""
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from ..core.base_agent import BASE_DIR

DEFAULT_LIBRARY_DIR = Path(os.getenv("TEMPLATE_LIBRARY_DIR", str(BASE_DIR.parent / "template_library")))


class TemplateRepository:
    backend = "abstract"

    def classify_tree(self) -> dict:
        raise NotImplementedError

    def list_by_prefix(self, prefix: str) -> List[dict]:
        """按 template_id 前缀召回（如 Engineering → Engineering_*；Lease → Lease*）"""
        raise NotImplementedError

    def list_all(self) -> List[dict]:
        raise NotImplementedError

    def enum_values(self, fields=("party_type", "region", "complexity", "scene")) -> Dict[str, List[str]]:
        enum = {k: set() for k in fields}
        for tpl in self.list_all():
            for k in fields:
                v = str(tpl.get(k, "") or "").strip()
                if v:
                    enum[k].add(v)
        return {k: sorted(v) for k, v in enum.items()}

    def add_template(self, template_id: str, record: dict) -> str:
        raise NotImplementedError

    def health(self) -> dict:
        return {"backend": self.backend, "ok": True}


def _flatten_file(path: Path) -> List[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[TemplateRepo] 读取模板文件失败 {path.name}: {e}", file=sys.stderr)
        return []
    out = []
    if isinstance(data, dict):
        if data and all(str(k).isdigit() for k in data.keys()):
            for key, value in data.items():
                if isinstance(value, dict) and "template_name" in value:
                    v = dict(value)
                    v["_file"], v["_key"] = str(path), key
                    out.append(v)
        elif "template_name" in data:
            v = dict(data)
            v["_file"], v["_key"] = str(path), "0"
            out.append(v)
    return out


class FileTemplateRepository(TemplateRepository):
    backend = "file"

    def __init__(self, library_dir: Optional[Path] = None, ttl: float = 300):
        self.dir = Path(library_dir or DEFAULT_LIBRARY_DIR)
        self.ttl = ttl
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    def _cached(self, key, loader):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < self.ttl:
                return hit[1]
        val = loader()
        with self._lock:
            self._cache[key] = (time.time(), val)
        return val

    def classify_tree(self) -> dict:
        def _load():
            p = self.dir / "all_classify.json"
            if not p.exists():
                return {}
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        return self._cached("classify", _load)

    def _files(self) -> List[Path]:
        if not self.dir.exists():
            return []
        return sorted(p for p in self.dir.glob("*.json") if p.name != "all_classify.json")

    def list_all(self) -> List[dict]:
        return [dict(x) for x in self._cached("all", lambda: [t for p in self._files() for t in _flatten_file(p)])]

    def list_by_prefix(self, prefix: str) -> List[dict]:
        out = []
        for p in self._files():
            stem = p.stem
            if stem == prefix or stem.startswith(prefix + "_"):
                out.extend(_flatten_file(p))
        return out

    def add_template(self, template_id: str, record: dict) -> str:
        from ..runtime.paths import atomic_write_json
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{template_id}.json"
        with self._lock:
            existing = {}
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = {}
            key = str(max([int(k) for k in existing.keys() if str(k).isdigit()] + [-1]) + 1)
            existing[key] = {k: v for k, v in record.items() if not k.startswith("_")}
            atomic_write_json(path, existing, indent=4)
            self._cache.clear()
        return key

    def health(self) -> dict:
        return {"backend": self.backend, "ok": self.dir.exists(), "dir": str(self.dir),
                "files": len(self._files())}


_repo: Optional[TemplateRepository] = None
_repo_lock = threading.Lock()


def get_template_repository() -> TemplateRepository:
    """TEMPLATE_BACKEND=mysql|file|auto（默认 auto：配置了 MySQL 且可连通则用 MySQL，否则文件）"""
    global _repo
    with _repo_lock:
        if _repo is not None:
            return _repo
        backend = os.getenv("TEMPLATE_BACKEND", "auto").lower()
        if backend in ("mysql", "auto"):
            try:
                from .mysql_template_repo import MySQLTemplateRepository
                repo = MySQLTemplateRepository.from_env()
                if repo is not None and repo.health().get("ok") and repo.count() > 0:
                    _repo = repo
                    return _repo
                if backend == "mysql":
                    print("[TemplateRepo] MySQL 模板库不可用或为空，回退到文件模板库", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                if backend == "mysql":
                    print(f"[TemplateRepo] MySQL 初始化失败，回退到文件模板库：{e}", file=sys.stderr)
        _repo = FileTemplateRepository()
        return _repo


def reset_template_repository(repo: Optional[TemplateRepository] = None):
    global _repo
    with _repo_lock:
        _repo = repo
