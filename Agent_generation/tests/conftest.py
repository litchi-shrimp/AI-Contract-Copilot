import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TEMPLATE_BACKEND", "file")
os.environ.setdefault("DeepSeekKey", "test-key")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    from agent.runtime import llm_gateway
    from agent.runtime.breaker import reset_all_breakers
    from agent.utils import web_search_core
    reset_all_breakers()
    monkeypatch.setattr(llm_gateway, "_sleep_backoff", lambda *a, **k: None)
    web_search_core.set_mock_search(lambda q, n: [{"title": "参考", "url": "http://x", "summary": "合同参考",
                                                   "content": "第一条 标的 第二条 价款", "ok": True}])
    yield
    llm_gateway.set_mock_handler(None)
    web_search_core.set_mock_search(None)
    web_search_core._cache.clear()


@pytest.fixture
def ctx(tmp_path):
    from agent.runtime import new_context, use_context
    events = []

    def sink(event_type, step=None, agent=None, data=None):
        events.append({"type": event_type, "step": step, "agent": agent, "data": data})

    c = new_context(workspace=tmp_path / "ws", sink=sink, tags={"events": events})
    with use_context(c):
        yield _Ctx(c, events)


class _Ctx:
    def __init__(self, ctx, events):
        self._ctx, self.events = ctx, events

    def __getattr__(self, item):
        return getattr(self._ctx, item)
