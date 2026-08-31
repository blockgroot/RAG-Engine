"""Admission test for ``app/llm/catalog.py`` — run before trusting the list.

A model id is not a capability. This codebase's grounding depends on behaviour
no catalogue can tell you about:

- the pipeline parses ``MODE: A|B|C`` off the FRONT of every generation
  (``app/rag/pipeline.py``). A model that opens with a ``<think>`` block or
  "Sure, here's…" produces no match, which does not fail loudly — it leaves
  ``mode=None``, and the groundedness audit only runs for modes A and B. The
  validation layer disappears silently.
- ``GitHubAgent`` grounds structurally: no tool call returns the fixed
  fallback. A model without function calling answers every GitHub question
  with the fallback and reads as a broken product.
- ``data_collection: "deny"`` can leave a free model with ZERO eligible
  endpoints ("No endpoints found matching your data policy") — which is the
  correct outcome for tenant data, and means the model cannot be offered.

So each candidate is probed for exactly those three things. Free models rotate
out without warning, so re-run this when the picker starts misbehaving.

    OPENROUTER_API_KEY=... python scripts/verify_models.py
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config.settings import GroqSettings, OpenRouterSettings  # noqa: E402
from app.llm.catalog import ALL_MODELS, BACKEND_GROQ  # noqa: E402
from app.llm.openai_provider import OpenAICompatProvider  # noqa: E402
from app.config.settings import RagSettings  # noqa: E402
from app.llm.routed import _ROUTING_PREFS  # noqa: E402

# The same anchor the pipeline uses. Imported by shape rather than by import so
# this script keeps working if the pipeline's regex is loosened.
MODE_RE = re.compile(r"^\s*MODE:\s*([ABC])\s*\n+(.*)", re.IGNORECASE | re.DOTALL)

# Production's answer cap. Sending the prompt WITHOUT it is what let a model
# that cannot work here pass verification: a reasoning model spends the whole
# cap on internal reasoning tokens and returns empty content with
# finish_reason="length". Unbounded, it eventually produces text and looks
# fine. An admission test that does not reproduce production's limits does not
# test production.
_ANSWER_CAP = RagSettings.from_env().max_answer_tokens

MODE_PROMPT = (
    "Begin your reply with a mode tag on its own line — 'MODE: A' — then a "
    "blank line, then a one-sentence answer.\n\nQUESTION: What colour is the "
    "sky on a clear day?"
)

TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_city",
        "description": "Look up a city record.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


def probe(model_id: str, settings, *, extra_body: dict | None = None) -> dict:
    """Run all three checks for one model. Never raises — a failure IS data."""
    result = {"model": model_id, "resolved": None, "content": False,
              "mode_tag": False, "tools": False, "error": None}
    client = OpenAICompatProvider(
        model=model_id,
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout,
        extra_body=extra_body,
    )
    try:
        text = client.generate(MODE_PROMPT, max_tokens=_ANSWER_CAP)
        result["resolved"] = client.last_resolved_model
        result["content"] = bool(text and text.strip())
        result["mode_tag"] = bool(MODE_RE.match(text or ""))
    except Exception as exc:  # noqa: BLE001 - the failure is the finding
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    try:
        chat = client.generate_with_tools(
            [{"role": "user", "content": "Look up the city record for Paris."}],
            tools=[TOOL],
            tool_choice="auto",
        )
        result["tools"] = bool(chat.tool_calls)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"tools: {type(exc).__name__}: {exc}"
    return result


def main() -> int:
    openrouter = OpenRouterSettings.from_env()
    groq = GroqSettings.from_env()
    if not (openrouter.enabled or groq.enabled):
        print("Neither OPENROUTER_API_KEY nor GROQ_API_KEY is set.")
        return 2

    rows = []
    for choice in ALL_MODELS:
        # Each backend is probed with its OWN credentials and its own request
        # extras. OpenRouter's `provider`/`reasoning` blocks are its request
        # extensions, not part of the OpenAI schema, so they are not sent to
        # Groq.
        if choice.backend == BACKEND_GROQ:
            if not groq.enabled:
                print(f"SKIP  {choice.id} (GROQ_API_KEY unset)")
                continue
            rows.append((choice, probe(choice.id, groq)))
        else:
            if not openrouter.enabled:
                print(f"SKIP  {choice.id} (OPENROUTER_API_KEY unset)")
                continue
            rows.append((choice, probe(choice.id, openrouter, extra_body=_ROUTING_PREFS)))

    failures = 0
    for choice, row in rows:
        ok = row["grounds"] and row["refuses"] and row["resists"] and row["tools"]
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  [{choice.backend}] {row['model']}")
        print(f"      resolved={row['resolved']}")
        print(f"      grounds={row['grounds']} (mode={row['mode']})  "
              f"refuses={row['refuses']}  resists={row['resists']}  "
              f"tools={row['tools']}")
        if row["error"]:
            print(f"      error: {row['error']}")
        if row["grounds"] and not row["refuses"]:
            print("      ^ ANSWERS WHAT IT SHOULD REFUSE, or paraphrases the")
            print("        fallback. The pipeline compares the fallback by string")
            print("        equality, so this counts as a grounded answer.")
            print(f"        got: {row['refusal_text']!r}")
        if not row["resists"]:
            print("      ^ COMPLIED WITH AN INJECTION in retrieved content.")
        if row["grounds"] and row["mode"] is None:
            print("      ^ no MODE tag — the groundedness audit would never run.")
    print(f"\n{len(rows) - failures}/{len(rows)} usable.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
