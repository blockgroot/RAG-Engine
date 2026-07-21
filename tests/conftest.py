"""Shared pytest fixtures for the Phase 2 vector-store tests.

These tests need a real Postgres+pgvector database (isolation can only be *proven*
against the real store, not a mock). They are skipped automatically if
``DATABASE_URL`` is not set. See README / CLAUDE.md for how to start a local DB.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow `pytest` from the project root to import the app package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.config.settings import DatabaseSettings, LLMSettings  # noqa: E402
from app.db import apply_schema, close_pool, get_connection  # noqa: E402
from app.embeddings import build_embedding_provider  # noqa: E402
from app.memory import build_conversation_store  # noqa: E402
from app.rag import build_rag_pipeline  # noqa: E402
from app.vectorstore import build_vector_store  # noqa: E402
from app.websearch import build_web_search_provider  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _close_pool_at_session_end():
    """Release the shared connection pool when the test session finishes."""
    yield
    close_pool()


def _database_available() -> bool:
    return bool(DatabaseSettings.from_env().url)


requires_db = pytest.mark.skipif(
    not _database_available(),
    reason="DATABASE_URL not set — start a Postgres+pgvector DB (see README).",
)


def _llm_available() -> bool:
    return bool(LLMSettings.from_env().model)


requires_llm = pytest.mark.skipif(
    not _llm_available(),
    reason="LLM not configured — set LLM_MODEL/LLM_API_KEY/LLM_BASE_URL (see README).",
)


@pytest.fixture(scope="session")
def embedder():
    """Real local embedding provider (BGE-M3). Loaded once for the session."""
    return build_embedding_provider()


@pytest.fixture(scope="session")
def store():
    """The vector store, with schema ensured."""
    apply_schema()
    return build_vector_store()


@pytest.fixture(scope="session")
def rag(embedder, store):
    """Phase 3 pipeline: pure retrieve-gate-generate (memory + web search OFF), so
    the grounding tests stay deterministic and unaffected by Phase 5."""
    return build_rag_pipeline(embedder=embedder, store=store, memory=None, web_search=None)


@pytest.fixture(scope="session")
def memory():
    """Conversation store (Postgres-backed)."""
    return build_conversation_store()


@pytest.fixture(scope="session")
def rag_convo(embedder, store, memory):
    """Phase 5 pipeline with conversation memory ON, web search OFF."""
    return build_rag_pipeline(
        embedder=embedder, store=store, memory=memory, web_search=None
    )


@pytest.fixture(scope="session")
def rag_web(embedder, store):
    """Phase 5 pipeline with the real web-search tool ON, memory OFF."""
    return build_rag_pipeline(
        embedder=embedder,
        store=store,
        memory=None,
        web_search=build_web_search_provider(),
    )


@pytest.fixture
def org_cleanup():
    """Track org_ids created during a test and cascade-delete them afterwards."""
    created: list[str] = []
    yield created
    if created:
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM organizations WHERE id = ANY(%s::uuid[])",
                (created,),
            )
