"""开发/演示用：以 Fake LLM 启动后端（不消耗 API 额度），用于前端联调与 Playwright E2E

    python -m server.devserver --port 8001

FAKE_DELAY 控制每次 LLM 调用的模拟耗时（秒），便于测试“停止 / 继续”
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--db", default="mysql+pymysql://root:123456@127.0.0.1:3306/contract_copilot_e2e?charset=utf8mb4")
    a = ap.parse_args()
    os.environ["DATABASE_URL"] = a.db
    os.environ.setdefault("TEMPLATE_BACKEND", "file")
    os.environ.setdefault("CORS_ORIGINS", "http://127.0.0.1:4173,http://localhost:4173,http://127.0.0.1:5173")
    os.environ.setdefault("WORKSPACES_DIR", str(ROOT / "workspaces_e2e"))
    from agent.runtime import set_mock_handler
    from fake_llm import FakeLLM
    set_mock_handler(FakeLLM(delay=float(os.getenv("FAKE_DELAY", "0.25"))))
    import uvicorn
    from server.app import app
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
