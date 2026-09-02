from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import get_settings
from app.database import init_db

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.is_production:
        if settings.SECRET_KEY == "change-me-in-development":
            raise RuntimeError("SECRET_KEY must be set in production.")
        if settings.uses_sqlite:
            raise RuntimeError(
                "DATABASE_URL must be a PostgreSQL URL in production (Render)."
            )
    if settings.ENV.lower() != "test":
        init_db()
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.APP_NAME,
        version="0.1.13",
        description="SkinsCaddy backend — accounts, friends, chats, wallet, challenges, feed, photos, scramble, push, and admin.",
        lifespan=lifespan,
    )
    origins = settings.cors_origin_list
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()


def _push_configured() -> bool:
    try:
        from app.services.push import fcm_configured

        return fcm_configured()
    except Exception:
        return False


@app.get("/health")
def health() -> dict:
    from app.services.email import smtp_configured

    return {
        "ok": True,
        "service": "skinscaddy",
        "env": settings.ENV,
        "database": "sqlite" if settings.uses_sqlite else "postgresql",
        "welcome_bonus": 100,
        "admin": True,
        "chats": True,
        "honor": True,
        "push": True,
        "honor_routes": [
            "/api/v1/honor",
            "/api/v1/honor/sync",
            "/api/v1/honor/friends",
            "/api/v1/honor/hot",
        ],
        "mail_configured": smtp_configured(),
        "push_configured": _push_configured(),
        "version": "0.1.13",
    }
