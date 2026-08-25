from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "SkinsCaddy API"
    ENV: str = "development"
    SECRET_KEY: str = "change-me-in-development"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    # Local default: file SQLite (no Postgres required).
    # Render: set DATABASE_URL to the instance URL (postgres:// is rewritten).
    DATABASE_URL: str = f"sqlite+pysqlite:///{(_BACKEND_DIR / 'skinscaddy.db').as_posix()}"
    CORS_ORIGINS: str = "*"
    PHOTO_DIR: str = str(_BACKEND_DIR / "var" / "photos")
    PHOTO_MAX_BYTES: int = 5 * 1024 * 1024
    PORT: int = 8000
    ADMIN_USERNAME: str = "justinv"
    # Render dashboard uses APP_BASE_URL; PUBLIC_BASE_URL is the local alias.
    APP_BASE_URL: str = ""
    PUBLIC_BASE_URL: str = "http://127.0.0.1:8000"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    VERIFICATION_HOURS: int = 48

    @property
    def photo_dir(self) -> Path:
        path = Path(self.PHOTO_DIR)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def is_production(self) -> bool:
        return self.ENV.lower() in {"prod", "production"}

    @property
    def cors_origin_list(self) -> list[str]:
        raw = (self.CORS_ORIGINS or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def uses_sqlite(self) -> bool:
        return self.sqlalchemy_database_url.startswith("sqlite")

    @property
    def sqlalchemy_database_url(self) -> str:
        url = (self.DATABASE_URL or "").strip()
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://") :]
        elif url.startswith("postgresql://") and "+psycopg" not in url.split("://", 1)[0]:
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        if url.startswith("sqlite") and ":///" in url:
            prefix, _, path = url.partition(":///")
            # Relative file (skinscaddy.db) always lives in backend/, not cwd.
            if path and not path.startswith("/") and ":/" not in path:
                url = f"{prefix}:///{(_BACKEND_DIR / path).as_posix()}"
            return url
        if (
            self.is_production
            and url.startswith("postgresql")
            and "sslmode=" not in url
        ):
            url += ("&" if "?" in url else "?") + "sslmode=require"
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
