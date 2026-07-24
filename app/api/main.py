"""FastAPI application entrypoint (Phase 13).

Run with: ``uvicorn app.api.main:app --host 0.0.0.0 --port 8000``

Every route in this package reaches for existing ``app/`` interfaces via their
``build_*()`` factories — this layer adds HTTP, sessions, and CORS; it never
duplicates provider/pipeline logic. CORS is scoped to the exact configured
frontend origin(s) (``API_CORS_ORIGINS``) with credentials allowed (the
session cookie); an empty configured origin list means no cross-origin
frontend can call this API with credentials, which is the safe default, not
a wildcard.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config.settings import ApiSettings
from . import admin as admin_router
from . import auth as auth_router
from . import orgs as orgs_router


def create_app() -> FastAPI:
    settings = ApiSettings.from_env()
    app = FastAPI(title="RAG Engine API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router.router)
    app.include_router(orgs_router.router)
    app.include_router(admin_router.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
