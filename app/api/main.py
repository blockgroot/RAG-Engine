"""FastAPI entrypoint: ``uvicorn app.api.main:app``.

Adds HTTP, sessions and CORS over the ``build_*()`` factories. CORS allows
only ``API_CORS_ORIGINS`` with credentials (empty = none). Ingest jobs drain
in a daemon thread here unless ``INGEST_WORKER_IN_API=false`` (then run
``scripts/run_worker.py``).
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config.settings import ApiSettings, IngestWorkerSettings, SchedulerSettings
from ..db import close_pool
from ..rag import shutdown_summary_folds
from ..security.rate_limit import limit_request, report_store
from . import admin as admin_router
from . import connector_apps as connector_apps_router
from . import internal as internal_router
from . import account as account_router
from . import actions as actions_router
from . import mcp_actions as mcp_actions_router
from . import mcp_host as mcp_host_router
from . import slack_act as slack_act_router
from . import notifications as notifications_router
from . import llm_model as llm_model_router
from . import auth as auth_router
from . import attachments as attachments_router
from . import chat as chat_router
from . import feedback as feedback_router
from . import slack_events as slack_events_router
from . import webhooks as webhooks_router
from . import orgs as orgs_router
from . import insights as insights_router
from . import schedulers as schedulers_router
from . import workspaces as workspaces_router
from . import updates as updates_router

# uvicorn only imports this module, so .env is loaded here before any
# from_env(); otherwise settings silently fall back to defaults.
load_dotenv()

logger = logging.getLogger(__name__)


def _start_in_api_worker(stop: threading.Event) -> threading.Thread:
    """Drain ``ingestion_jobs`` in-process and tick the schedulers on the same thread.

    One loop, no new infra; ingest polls every few seconds, schedulers every few minutes.
    """
    from ..jobs import queue
    from ..jobs.autosync import start_cooled_down_pushes
    from ..jobs.worker import (
        PUSH_CHECK_SECONDS,
        run_once,
        run_scheduler_tick,
    )

    worker_settings = IngestWorkerSettings.from_env()
    poll_interval = worker_settings.poll_seconds
    reap_interval = worker_settings.reap_seconds
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
        last_push = 0.0
        # Start at -poll_seconds so a scheduler due during downtime runs right after boot.
        last_scheduler = -float(scheduler_settings.poll_seconds)
        while not stop.is_set():
            try:
                now = time.monotonic()
                if now - last_reap >= reap_interval:
                    queue.reap_stuck()
                    last_reap = now
                if now - last_push >= PUSH_CHECK_SECONDS:
                    # A push inside the cooldown waits ~3 min here, not ~10 for the tick.
                    start_cooled_down_pushes()
                    last_push = now
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
    """Load the embedder and reranker on a background thread at startup.

    Otherwise the first question pays the model load. Non-blocking so the API
    serves meanwhile; failures are swallowed (a real request will report them).
    Off with ``MODEL_WARMUP_ON_STARTUP=false`` on low-RAM machines.
    """
    if not ApiSettings.from_env().model_warmup:
        return None

    def _warm() -> None:
        try:
            from ..embeddings import build_embedding_provider

            # One encode also pays the lazy CUDA/MPS graph setup.
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
    try:
        report_store()  # one line saying where rate limits are counted
    except Exception:  # noqa: BLE001 - a report must never stop startup
        logger.exception("Rate limit store report failed")
    if IngestWorkerSettings.from_env().in_api:
        worker = _start_in_api_worker(stop)
    try:
        yield
    finally:
        stop.set()
        if worker is not None:
            worker.join(timeout=8)
        # Finish any in-flight summary folds before dropping the pool
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

    # Every route a person calls takes their request bucket (per IP when
    # signed out), so a new route is limited without anyone adding it.
    for router in (
        auth_router,
        orgs_router,
        admin_router,
        connector_apps_router,
        llm_model_router,
        updates_router,
        chat_router,
        attachments_router,
        feedback_router,
        workspaces_router,
        schedulers_router,
        insights_router,
        notifications_router,
        account_router,
        actions_router,
        mcp_actions_router,
    ):
        app.include_router(router.router, dependencies=[Depends(limit_request)])
    # Not a person: the cron tick, our MCP servers and signed webhooks.
    # Per-IP limits would drop real events.
    app.include_router(internal_router.router)
    app.include_router(mcp_host_router.router)
    app.include_router(slack_act_router.router)
    app.include_router(slack_events_router.router)
    app.include_router(webhooks_router.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
