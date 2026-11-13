"""The models a member may pick from, and the one they get by default.

A hardcoded, verified list, not a live ``GET /models``: grounding depends
on behaviour an id cannot promise (the ``MODE:`` tag, the exact fallback,
function calling). ``scripts/verify_models.py`` revalidates it.

``AUTO`` is not a model: it means "no override", the configured default.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The dropdown's default value. Sentinel, never sent to any API.
AUTO = "auto"


#: Which endpoint serves a model. Several, so one exhausted quota does not
#: empty the picker.
BACKEND_OPENROUTER = "openrouter"
BACKEND_GROQ = "groq"
BACKEND_OLLAMA = "ollama"
BACKEND_GEMINI = "gemini"
BACKEND_CLAUDE = "claude"


@dataclass(frozen=True)
class ModelChoice:
    """One selectable model, as the picker and the API both see it."""

    #: Model id, sent verbatim as ``model`` to whichever backend serves it.
    id: str
    #: What the dropdown shows.
    label: str
    #: One line under the label — why someone would pick this one.
    note: str = ""
    #: Which endpoint to send it to: the id alone cannot say (two backends can
    #: serve one model under different slugs).
    backend: str = BACKEND_OPENROUTER
    #: Builds charts: returns the chart classifier's strict JSON reliably. Others
    #: answered in prose and the refusal blamed the member's question.
    charts: bool = False
    #: Extra request fields (e.g. reasoning off): a model that spends the
    #: answer cap thinking returns EMPTY content.
    extra_body: dict | None = None


# Free-tier OpenRouter ids must pass scripts/verify_models.py against a live
# key (content, a parseable MODE tag, a real tool-call round trip). Excluded
# classes: ids refused by ``data_collection: "deny"`` (they train on prompts),
# REASONING models that spend the answer cap and return empty content
# (``reasoning.exclude`` does not stop that), and models with intermittent
# tool calls (GitHub answers would fall back).
#: None offered since 9 Oct 2026 (the free tier is 50 requests/day
#: account-wide). The backend stays for an org's own OpenRouter model.
MODELS: tuple[ModelChoice, ...] = ()

#: Groq, Gemini and Ollama: none offered. Production answers on Claude only;
#: background work runs on Gemini as the AUX model, never picked here. The
#: backends stay for re-adding a model verified by scripts/verify_models.py.
GROQ_MODELS: tuple[ModelChoice, ...] = ()
GEMINI_MODELS: tuple[ModelChoice, ...] = ()
OLLAMA_MODELS: tuple[ModelChoice, ...] = ()

#: Claude, on the deployment's Claude key (``ClaudeSettings``). Both build
#: charts and are priced in ``AI_PRICES`` (``security/ai_spend.py``).
CLAUDE_MODELS: tuple[ModelChoice, ...] = (
    ModelChoice(
        id="claude-sonnet-5-5",
        label="Claude Sonnet 5.5",
        note="Strongest answers.",
        backend=BACKEND_CLAUDE,
        charts=True,
    ),
    ModelChoice(
        id="claude-haiku-5-5",
        label="Claude Haiku 5.5",
        note="Fast and low cost.",
        backend=BACKEND_CLAUDE,
        charts=True,
    ),
)

ALL_MODELS: tuple[ModelChoice, ...] = CLAUDE_MODELS + GEMINI_MODELS + OLLAMA_MODELS + MODELS + GROQ_MODELS

_BY_ID = {m.id: m for m in ALL_MODELS}


def is_selectable(model_id: str | None) -> bool:
    """True for ``None``/``auto`` (no override) or a catalogued id. A
    client-supplied model string is untrusted input."""
    # Mirrors ``normalize``: model="" means "no preference", not a 400.
    if not model_id or model_id == AUTO:
        return True
    return model_id in _BY_ID


def normalize(model_id: str | None) -> str | None:
    """Collapse ``auto``/blank to ``None``, so "no override" has one spelling
    downstream."""
    if not model_id or model_id == AUTO:
        return None
    return model_id


def get(model_id: str) -> ModelChoice | None:
    """The catalogued choice for an id, or ``None`` if it is not offered."""
    return _BY_ID.get(model_id)


def supports_charts(model_id: str | None) -> bool:
    """True for the default model, an org's own model (not ours to judge), or
    a catalogued model verified to build charts."""
    choice = _BY_ID.get(model_id) if model_id else None
    return choice is None or choice.charts


def chart_models() -> list[ModelChoice]:
    """Catalogued models that build charts, to name in the refusal."""
    return [m for m in ALL_MODELS if m.charts]


def available(backends: set[str]) -> list[ModelChoice]:
    """Only models whose backend has credentials: an unconfigured one would
    fall back to the default and answer under a false label."""
    return [m for m in ALL_MODELS if m.backend in backends]


def as_dicts(backends: set[str] | None = None) -> list[dict]:
    """Catalog shape for ``GET /chat/models``."""
    models = available(backends) if backends is not None else list(ALL_MODELS)
    return [
        {"id": m.id, "label": m.label, "note": m.note, "backend": m.backend,
         "charts": m.charts}
        for m in models
    ]
