"""会话工作区路径解析 + 原子写

有 RunContext.workspace 时，所有中间产物写入 <workspace>/{outputs,data,memory}；
无上下文（CLI / 调试脚本）时退回项目原有目录，保持向后兼容。
"""
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .context import current

AGENT_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = AGENT_DIR.parent

_LEGACY = {
    "outputs": BASE_DIR / "outputs",
    "data": AGENT_DIR / "data",
    "memory": AGENT_DIR / "memory",
}


def _resolve(kind: str) -> Path:
    ctx = current()
    if ctx is not None and ctx.workspace is not None:
        d = ctx.workspace / kind
    else:
        d = _LEGACY[kind]
    d.mkdir(parents=True, exist_ok=True)
    return d


def outputs_dir() -> Path:
    return _resolve("outputs")


def data_dir() -> Path:
    return _resolve("data")


def memory_dir() -> Path:
    return _resolve("memory")


_write_lock = threading.Lock()


def atomic_write_text(path: Path, text: str):
    """原子写：先写同目录临时文件再 os.replace，进程崩溃不会留下半截文件"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        with _write_lock:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def atomic_write_json(path: Path, data: Any, indent: int = 2):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent))
