"""Which dataset adapters run. Callers depend on this and ``base``, never on a
concrete adapter (CLAUDE.md §2)."""

from __future__ import annotations

from ..config.settings import DocTablesSettings
from .base import DatasetAdapter
from .table_adapter import TableAdapter


def build_dataset_adapters(
    settings: DocTablesSettings | None = None, llm=None,
) -> list[DatasetAdapter]:
    """The table adapter always; the text adapter when switched on.

    ``llm`` is only needed by the text adapter, and only when it actually
    runs (on the tick); ingestion never passes one.
    """
    settings = settings or DocTablesSettings.from_env()
    adapters: list[DatasetAdapter] = [TableAdapter()]
    if settings.text_enabled:
        from .text_adapter import TextAdapter

        adapters.append(TextAdapter(llm=llm, max_chars=settings.text_max_chars))
    return adapters


def build_upload_text_adapter(llm, settings: DocTablesSettings | None = None) -> DatasetAdapter:
    """The text adapter for a file uploaded to a chat, run when the asker is
    waiting in Chart mode -- so not gated on ``text_enabled``, which budgets
    BACKGROUND quota for the indexed library. Interactive, once per file."""
    from .text_adapter import TextAdapter

    settings = settings or DocTablesSettings.from_env()
    return TextAdapter(llm=llm, max_chars=settings.text_max_chars)
