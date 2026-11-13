"""Which dataset adapters run. Callers depend on this and ``base``, never on a
concrete adapter (CLAUDE.md §2)."""

from __future__ import annotations

from ..config.settings import DocTablesSettings
from .base import DatasetAdapter
from .table_adapter import TableAdapter


def build_dataset_adapters(settings: DocTablesSettings | None = None) -> list[DatasetAdapter]:
    """What ingestion runs: the table adapter always; the text adapter when on.
    Built without a model, it uses the aux model after a background slot, so
    ingest never takes a waiting person's quota."""
    settings = settings or DocTablesSettings.from_env()
    adapters: list[DatasetAdapter] = [TableAdapter()]
    if settings.text_enabled:
        from .text_adapter import TextAdapter

        adapters.append(TextAdapter(max_chars=settings.text_max_chars))
    return adapters


def build_upload_text_adapter(llm, settings: DocTablesSettings | None = None) -> DatasetAdapter:
    """The text adapter for a read made while someone waits in Chart mode (an
    uploaded file, a named page): the interactive model, not gated on
    ``text_enabled``, and the looser figures check (``wants_for_chart``)."""
    from .text_adapter import TextAdapter

    settings = settings or DocTablesSettings.from_env()
    return TextAdapter(llm=llm, max_chars=settings.text_max_chars, interactive=True)
