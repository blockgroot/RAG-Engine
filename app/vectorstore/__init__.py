"""Tenant-scoped vector store abstraction.

Public API:
    from app.vectorstore import build_vector_store
    store = build_vector_store()
    org_id = store.create_organization("Acme")
    doc_id = store.add_document(org_id, "Leave Policy", chunks, embeddings)
    hits = store.query(org_id, query_embedding, top_k=3)
"""

from .base import VectorStore, RetrievedChunk, OrganizationRef
from .pgvector_store import PgVectorStore
from .factory import build_vector_store

__all__ = [
    "VectorStore",
    "RetrievedChunk",
    "OrganizationRef",
    "PgVectorStore",
    "build_vector_store",
]
