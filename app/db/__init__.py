"""Database infrastructure: connections and schema management.

This package holds the raw Postgres/pgvector plumbing. The rest of the app does
not import from here directly — it talks to the vector store abstraction
(``app.vectorstore``), which uses these helpers internally.
"""

from .connection import get_connection, DatabaseError
from .migrate import apply_schema

__all__ = ["get_connection", "DatabaseError", "apply_schema"]
