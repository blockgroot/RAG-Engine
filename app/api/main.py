"""FastAPI application entrypoint (Phase 13).

Run with: ``uvicorn app.api.main:app --host 0.0.0.0 --port 8000``

Every route in this package reaches for existing ``app/`` interfaces via their
``build_*()`` factories — this layer adds HTTP, sessions, and CORS; it never
duplicates provider/pipeline logic. CORS is scoped to the exact configured
frontend origin(s) (``API_CORS_ORIGINS``) with credentials allowed (the
session cookie); an empty configured origin list means no cross-origin
frontend can call this API with credentials, which is the safe default, not
a wildcard.

Ingestion jobs use the Postgres ``ingestion_jobs`` queue (``SELECT … FOR
UPDATE SKIP LOCKED``). By default a daemon thread inside this process drains
that queue (``INGEST_WORKER_IN_API=true``) so local/dev needs no second
terminal. Set ``INGEST_WORKER_IN_API=false`` and run
``python scripts/run_worker.py`` when you want the worker in its own process
(heavier ingest loads, multi-replica API).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config.settings import ApiSettings, SchedulerSettings, env_bool
from ..db import close_pool
from ..rag import shutdown_summary_folds
from . import admin as admin_router
from . import llm_model as llm_model_router
from . import auth as auth_router
from . import chat as chat_router
from . import orgs as orgs_router
from . import schedulers as schedulers_router
from . import workspaces as workspaces_router

# Every scripts/*.py entrypoint calls this before reading settings; the ASGI
# app has no equivalent entrypoint of its own (uvicorn just imports this
# module), so it must load .env here, at import time, before ApiSettings.from_env()
# and every other from_env() call below it runs — otherwise every setting
# silently falls back to its default (e.g. an empty CORS origin list, which
# makes the browser's preflight fail with no useful error).
load_dotenv()

logger = logging.getLogger(__name__)


def _start_in_api_worker(stop: threading.Event) -> threading.Thread:
    """Drain ``ingestion_jobs`` in-process so Sync works without run_worker.py.

    Also ticks the activity scheduler on the same thread. Sharing one loop
    (rather than adding a second thread) keeps the deployment story unchanged
    — still one process, no new infra — and the two are never contended for:
    ingestion polls every couple of seconds, schedulers every few minutes.
    """
    from ..jobs import queue
    from ..jobs.worker import run_once, run_scheduler_tick

    poll_interval = float(os.getenv("INGEST_WORKER_POLL_SECONDS", "2"))
    reap_interval = float(os.getenv("INGEST_WORKER_REAP_SECONDS", "60"))
    scheduler_settings = SchedulerSettings.from_env()

    def _loop() -> None:
        logger.info(
            "In-API ingestion worker started (poll=%.1fs). "
            "Set INGEST_WORKER_IN_API=false to use scripts/run_worker.py instead.",
            poll_interval,
        )
        try:
            n = queue.requeue_interrupted_running()
            if n:
                logger.info(
                    "Re-queued %s interrupted ingestion job(s) after worker start",
                    n,
                )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to re-queue interrupted ingestion jobs")
        try:
            from ..jobs.scheduler_queue import requeue_interrupted_running

            n = requeue_interrupted_running(
                max_attempts=scheduler_settings.max_attempts
            )
            if n:
                logger.info("Re-queued %s interrupted scheduler run(s)", n)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to re-queue interrupted scheduler runs")
        last_reap = 0.0
        # Start at -poll_seconds so the first scheduler tick happens promptly
        # after boot rather than one whole interval later — a scheduler that
        # came due while the process was down should not wait 5 more minutes.
        last_scheduler = -float(scheduler_settings.poll_seconds)
        while not stop.is_set():
            try:
                now = time.monotonic()
                if now - last_reap >= reap_interval:
                    queue.reap_stuck()
                    last_reap = now
                if (
                    scheduler_settings.enabled
                    and now - last_scheduler >= scheduler_settings.poll_seconds
                ):
                    run_scheduler_tick(scheduler_settings)
                    last_scheduler = now
                job = run_once()
                if job is None:
                    stop.wait(poll_interval)
            except Exception:  # noqa: BLE001 — worker must survive one bad tick
                logger.exception("In-API ingestion worker tick failed")
                stop.wait(min(5.0, poll_interval * 2))
        logger.info("In-API ingestion worker stopped")

    thread = threading.Thread(target=_loop, name="ingest-worker", daemon=True)
    thread.start()
    return thread


def _start_model_warmup() -> threading.Thread | None:
    """Load the embedder (and reranker) in the background at startup.

    Both are multi-GB local models loaded lazily on first use, so without this
    the *first* person to ask a question pays the entire model-load cost inside
    their request — seconds of apparently-hung chat, once per process restart.
    Warming moves that cost to boot, where nobody is waiting on it.

    Runs on a daemon thread rather than blocking ``lifespan`` so the API still
    starts (and serves login, admin, and GitHub chat, none of which need these
    models) while the weights load. Failures are swallowed deliberately: warmup
    is an optimization, and a machine that cannot load the models should fail on
    the first retrieval request with a real error, not refuse to boot at all.

    Opt out with ``MODEL_WARMUP_ON_STARTUP=false`` — worth doing on a low-RAM
    machine, or when running the API purely for GitHub chat (see CLAUDE.md §4 on
    the 16GB-Mac hazard).
    """
    if not env_bool("MODEL_WARMUP_ON_STARTUP", default=True):
        return None

    def _warm() -> None:
        try:
            from ..embeddings import build_embedding_provider

            # Encode one trivial string: constructing the provider loads the
            # weights, but the first real encode still pays lazy CUDA/MPS graph
            # setup, so do both here rather than leaving half the cost behind.
            build_embedding_provider().embed(["warmup"])
        except Exception:  # noqa: BLE001 - warmup must never block startup
            logger.warning("embedding model warmup failed", exc_info=True)

        try:
            from ..config.settings import RetrievalSettings
            from ..reranker import build_reranker

            if RetrievalSettings.from_env().rerank_enabled:
                build_reranker()
        except Exception:  # noqa: BLE001
            logger.warning("reranker warmup failed", exc_info=True)

    thread = threading.Thread(target=_warm, name="model-warmup", daemon=True)
    thread.start()
    return thread


@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop = threading.Event()
    worker: threading.Thread | None = None
    _start_model_warmup()
    if env_bool("INGEST_WORKER_IN_API", default=True):
        worker = _start_in_api_worker(stop)
    try:
        yield
    finally:
        stop.set()
        if worker is not None:
            worker.join(timeout=8)
        # Phase 15: finish any in-flight summary folds before dropping the pool
        # so a mid-shutdown request doesn't silently lose bookkeeping.
        shutdown_summary_folds(wait=True, timeout=30.0)
        close_pool()


def create_app() -> FastAPI:
    settings = ApiSettings.from_env()
    app = FastAPI(title="RAG Engine API", lifespan=lifespan)

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
    app.include_router(llm_model_router.router)
    app.include_router(chat_router.router)
    app.include_router(workspaces_router.router)
    app.include_router(schedulers_router.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
