"""The models a member may pick from, and the one they get by default.

Deliberately a hardcoded tuple rather than a live ``GET /models`` fetch.
OpenRouter serves hundreds of models and rotates its free ones out without
warning, so a fetched list would be *unverified* — and this codebase's
grounding depends on model behaviour that a model id cannot tell you about:
the ``MODE: A|B|C`` tag the pipeline parses off the front of every generation
(``app/rag/pipeline.py``), the fixed fallback string compared by equality, and
function calling for ``GitHubAgent``. Five ids admitted by a golden-set run
beat three hundred that merely exist. ``scripts/verify_models.py``
is what revalidates this list.

``AUTO`` is not a model. It means "no override" — the deployment's configured
``LLM_MODEL``/``LLM_BASE_URL``, i.e. exactly today's behaviour for anyone who
never touches the dropdown.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The dropdown's default value. Sentinel, never sent to any API.
AUTO = "auto"


#: Which endpoint serves a model. Two, not one, because quota is the binding
#: constraint on a free tier: OpenRouter allows 50 requests/day account-wide
#: while Groq allows thousands, so drawing from both means one being exhausted
#: does not empty the picker.
BACKEND_OPENROUTER = "openrouter"
BACKEND_GROQ = "groq"


@dataclass(frozen=True)
class ModelChoice:
    """One selectable model, as the picker and the API both see it."""

    #: Model id, sent verbatim as ``model`` to whichever backend serves it.
    id: str
    #: What the dropdown shows.
    label: str
    #: One line under the label — why someone would pick this one.
    note: str = ""
    #: Which endpoint to send it to. The id alone cannot say: Groq and
    #: OpenRouter both serve Llama under different slugs, and only one of them
    #: accepts OpenRouter's routing preferences.
    backend: str = BACKEND_OPENROUTER


# Free-tier OpenRouter ids, each VERIFIED against a live key by
# scripts/verify_models.py: non-empty content, a parseable
# ``MODE: A|B|C`` tag, and a real tool-call round-trip.
#
# The first five ids tried here (deepseek-chat-v3.1:free, llama-3.3-70b:free,
# gemini-2.0-flash-exp:free, mistral-small-3.2:free, qwen3-235b:free) were all
# 404 — "unavailable for free, use the paid slug". That is the churn this
# module's docstring predicts, and it is why the list is verified rather than
# copied from a blog post. Re-run the script when the picker starts failing.
#
# Two more classes of exclusion, both discovered the same way:
#   - nvidia/* and poolside/* return "No endpoints found matching your data
#     policy (Free model training)" — every free provider for them retains or
#     trains on prompts, so ``data_collection: "deny"`` correctly refuses. They
#     CANNOT be offered without opting tenant content into training.
#   - z-ai/glm-5.2 and google/gemma-4-* returned provider 429s on every attempt
#     during testing. Not a capability failure; retry before adding them.
#   - dots-studio/dots-3-note-preview and openrouter/free are REASONING models
#     (the router picks them at random). With production's
#     ``RAG_MAX_ANSWER_TOKENS=700`` they spend the entire budget on internal
#     reasoning and return EMPTY content with finish_reason="length" — a hard
#     LLMProviderError, so chat shows an error rather than an answer. Note
#     ``reasoning: {"exclude": True}`` does NOT prevent this: it strips
#     reasoning from the RESPONSE, while the tokens are still generated and
#     still counted. Verified in production, and it is why the admission test
#     now sends the real answer cap.
#   - minimax/minimax-m3 and minimax/minimax-m2.7 answer and tag correctly but
#     their tool calls are INTERMITTENT — one probe returned tool calls, the
#     next returned no choices at all. Excluded on purpose: GitHubAgent grounds
#     structurally, so a model whose tool calling works most of the time
#     answers some GitHub questions with the fixed fallback and looks broken
#     rather than rate-limited. "Usually supports tools" is not support.
#
# Four entries, not five: a verified list is the point, and padding it with a
# model that fails a probe would defeat the reason this file is hardcoded.
MODELS: tuple[ModelChoice, ...] = (
    ModelChoice(
        id="inclusionai/ling-3.0-flash-fin:free",
        label="Ling 3.0 Flash",
        note="Fast, 262K context.",
    ),
    ModelChoice(
        id="cohere/north-mini-code:free",
        label="Cohere North Mini",
        note="Low latency, reliable tool calling.",
    ),
)

#: Groq-hosted models, each VERIFIED by scripts/verify_models.py against a
#: live key: grounds (Mode A), refuses VERBATIM on unanswerable context,
#: resists an injection planted in retrieved content, and makes a real tool
#: call — all under production's RAG_MAX_ANSWER_TOKENS cap.
#:
#: Rejected here, and why (re-probe before re-adding any of them):
#:   - llama-3.3-70b-versatile, llama-3.1-8b-instant: 404, no longer served.
#:     They were my guess from documentation, which is exactly the mistake
#:     that put five dead OpenRouter ids in the first version of this file.
#:   - qwen/qwen3.6-27b: refuses and resists correctly but emits NO MODE tag,
#:     so the groundedness audit would silently never run for it.
#:   - groq/compound-mini: 400 "`tool calling` is not supported" — it is an
#:     agentic system with its own built-in tools, so GitHubAgent would return
#:     the fixed fallback for every question.
#:   - whisper-*/orpheus-*/prompt-guard-*: not chat models.
GROQ_MODELS: tuple[ModelChoice, ...] = (
    ModelChoice(
        id="openai/gpt-oss-120b",
        label="GPT-OSS 120B",
        note="Largest on Groq; strongest on multi-part questions.",
        backend=BACKEND_GROQ,
    ),
    ModelChoice(
        id="openai/gpt-oss-20b",
        label="GPT-OSS 20B",
        note="Much faster, same family — a good default for quick questions.",
        backend=BACKEND_GROQ,
    ),
    ModelChoice(
        id="qwen/qwen3.8-27b",
        label="Qwen 3.8 27B",
        note="Strong general reasoning at low latency.",
        backend=BACKEND_GROQ,
    ),
)

ALL_MODELS: tuple[ModelChoice, ...] = MODELS + GROQ_MODELS

_BY_ID = {m.id: m for m in ALL_MODELS}


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


def get(model_id: str) -> ModelChoice | None:
    """The catalogued choice for an id, or ``None`` if it is not offered."""
    return _BY_ID.get(model_id)


def available(backends: set[str]) -> list[ModelChoice]:
    """Only models whose backend actually has credentials configured.

    A deployment with an OpenRouter key but no Groq key must not offer Groq
    models: they would fall back to the default model and answer under a label
    naming a model that never ran, which is worse than not offering them.
    """
    return [m for m in ALL_MODELS if m.backend in backends]


def as_dicts(backends: set[str] | None = None) -> list[dict]:
    """Catalog shape for ``GET /chat/models``."""
    models = available(backends) if backends is not None else list(ALL_MODELS)
    return [
        {"id": m.id, "label": m.label, "note": m.note, "backend": m.backend}
        for m in models
    ]
