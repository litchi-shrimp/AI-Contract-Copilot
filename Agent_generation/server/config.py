"""服务配置（环境变量）"""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Settings:
    database_url = os.getenv("DATABASE_URL", "mysql+pymysql://root:123456@127.0.0.1:3306/contract_copilot?charset=utf8mb4")
    jwt_secret = os.getenv("JWT_SECRET") or ""
    jwt_ttl_minutes = int(os.getenv("JWT_TTL_MINUTES", "720"))
    cors_origins = [o.strip() for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080"
    ).split(",") if o.strip()]
    cors_origin_regex = os.getenv("CORS_ORIGIN_REGEX", r"https://[a-z0-9-]+\.github\.io")
    workspaces_dir = Path(os.getenv("WORKSPACES_DIR", str(ROOT / "workspaces")))
    max_input_chars = int(os.getenv("MAX_INPUT_CHARS", "4000"))
    max_active_runs_per_user = int(os.getenv("MAX_ACTIVE_RUNS_PER_USER", "2"))
    max_active_runs_total = int(os.getenv("MAX_ACTIVE_RUNS_TOTAL", "16"))
    run_max_llm_calls = int(os.getenv("RUN_MAX_LLM_CALLS", "200"))
    run_max_tokens = int(os.getenv("RUN_MAX_TOKENS", "3000000"))
    idle_close_hours = float(os.getenv("IDLE_CLOSE_HOURS", "72"))
    allow_register = os.getenv("ALLOW_REGISTER", "1") == "1"
    login_max_failures = 5
    login_lock_minutes = 15


settings = Settings()

_secret_file = ROOT / ".jwt_secret"
if not settings.jwt_secret:
    # 未配置时生成并持久化到本地文件（重启后 token 仍有效）；生产环境应通过 JWT_SECRET 配置
    if _secret_file.exists():
        settings.jwt_secret = _secret_file.read_text(encoding="utf-8").strip()
    else:
        settings.jwt_secret = secrets.token_urlsafe(48)
        _secret_file.write_text(settings.jwt_secret, encoding="utf-8")
