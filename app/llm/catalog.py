"""The models a member may pick from, and the one they get by default.

Deliberately a hardcoded tuple rather than a live ``GET /models`` fetch.
OpenRouter serves hundreds of models and rotates its free ones out without
warning, so a fetched list would be *unverified* — and this codebase's
grounding depends on model behaviour that a model id cannot tell you about:
the ``MODE: A|B|C`` tag the pipeline parses off the front of every generation
(``app/rag/pipeline.py``), the fixed fallback string compared by equality, and
function calling for ``GitHubAgent``. Five ids admitted by a golden-set run
beat three hundred that merely exist. ``scripts/verify_openrouter_models.py``
is what revalidates this list.

``AUTO`` is not a model. It means "no override" — the deployment's configured
``LLM_MODEL``/``LLM_BASE_URL``, i.e. exactly today's behaviour for anyone who
never touches the dropdown.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The dropdown's default value. Sentinel, never sent to any API.
AUTO = "auto"


@dataclass(frozen=True)
class ModelChoice:
    """One selectable model, as the picker and the API both see it."""

    #: OpenRouter model id, sent verbatim as ``model``.
    id: str
    #: What the dropdown shows.
    label: str
    #: One line under the label — why someone would pick this one.
    note: str = ""


# Free-tier OpenRouter ids. Kept small on purpose (see module docstring).
# Every entry must pass scripts/verify_openrouter_models.py before it lands.
MODELS: tuple[ModelChoice, ...] = (
    ModelChoice(
        id="deepseek/deepseek-chat-v3.1:free",
        label="DeepSeek V3.1",
        note="Strong general reasoning, long context.",
    ),
    ModelChoice(
        id="meta-llama/llama-3.3-70b-instruct:free",
        label="Llama 3.3 70B",
        note="Open weights, reliable instruction following.",
    ),
    ModelChoice(
        id="google/gemini-2.0-flash-exp:free",
        label="Gemini 2.0 Flash",
        note="Fast, good on long retrieved context.",
    ),
    ModelChoice(
        id="mistralai/mistral-small-3.2-24b-instruct:free",
        label="Mistral Small 3.2",
        note="Low latency, solid function calling.",
    ),
    ModelChoice(
        id="qwen/qwen3-235b-a22b:free",
        label="Qwen3 235B",
        note="Largest of the five; best on multi-part questions.",
    ),
)

_BY_ID = {m.id: m for m in MODELS}


def is_selectable(model_id: str | None) -> bool:
    """True for ``None``/``auto`` (no override) or a catalogued id.

    The API validates against this before anything reaches an outbound call or
    a cache key — a client-supplied model string is untrusted input, exactly
    like every other field in a request body.
    """
    # Mirrors ``normalize`` exactly. A validator that rejects what the
    # normalizer would collapse is a trap: a frontend sending model="" would
    # get a 400 for what it means as "no preference".
    if not model_id or model_id == AUTO:
        return True
    return model_id in _BY_ID


def normalize(model_id: str | None) -> str | None:
    """Collapse ``auto``/blank to ``None`` — "no override" has one spelling.

    Everything downstream (the ContextVar, the cache key) then only ever sees
    ``None`` or a real id, so "auto" can never leak into a cache key and split
    it from the identical un-overridden request.
    """
    if not model_id or model_id == AUTO:
        return None
    return model_id


def as_dicts() -> list[dict]:
    """Catalog shape for ``GET /chat/models``."""
    return [{"id": m.id, "label": m.label, "note": m.note} for m in MODELS]
