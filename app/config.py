from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    """轻量加载项目根目录 .env (不引第三方库)。已存在的环境变量优先, 不覆盖。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_base_url: str
    host: str
    port: int
    database_path: Path
    upload_dir: Path
    log_dir: Path
    encryption_master_key: str
    allow_public_signup: bool
    max_upload_mb: int
    cookie_secure: bool
    cookie_same_site: str
    xunji_api_base_url: str
    llm_base_url: str
    llm_model: str
    owner_username: str
    owner_password: str


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    return int(value)


def load_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        app_base_url=os.getenv("APP_BASE_URL", "http://127.0.0.1:8000"),
        host=os.getenv("HOST", "127.0.0.1"),
        port=_int(os.getenv("PORT"), 8000),
        database_path=Path(os.getenv("DATABASE_PATH", "var/app.db")),
        upload_dir=Path(os.getenv("UPLOAD_DIR", "var/uploads")),
        log_dir=Path(os.getenv("LOG_DIR", "var/logs")),
        encryption_master_key=os.getenv("ENCRYPTION_MASTER_KEY", "dev-change-me-dev-change-me-32-bytes-min"),
        allow_public_signup=_bool(os.getenv("ALLOW_PUBLIC_SIGNUP"), False),
        max_upload_mb=_int(os.getenv("MAX_UPLOAD_MB"), 25),
        cookie_secure=_bool(os.getenv("COOKIE_SECURE"), False),
        cookie_same_site=os.getenv("COOKIE_SAME_SITE", "lax"),
        xunji_api_base_url=os.getenv("XUNJI_API_BASE_URL", "https://trains.xunjiapp.cn"),
        llm_base_url=os.getenv("LLM_BASE_URL", ""),
        llm_model=os.getenv("LLM_MODEL", ""),
        owner_username=os.getenv("OWNER_USERNAME", "owner"),
        owner_password=os.getenv("OWNER_PASSWORD", "change-me"),
    )


settings = load_settings()
