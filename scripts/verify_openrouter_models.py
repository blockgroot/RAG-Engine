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

    OPENROUTER_API_KEY=... python scripts/verify_openrouter_models.py
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config.settings import OpenRouterSettings  # noqa: E402
from app.llm.catalog import MODELS  # noqa: E402
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


def probe(model_id: str, settings: OpenRouterSettings) -> dict:
    """Run all three checks for one model. Never raises — a failure IS data."""
    result = {"model": model_id, "resolved": None, "content": False,
              "mode_tag": False, "tools": False, "error": None}
    client = OpenAICompatProvider(
        model=model_id,
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout,
        extra_body=_ROUTING_PREFS,
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
    settings = OpenRouterSettings.from_env()
    if not settings.enabled:
        print("OPENROUTER_API_KEY is not set — nothing to verify.")
        return 2

    print(f"Probing {len(MODELS)} catalogued models against {settings.base_url}\n")
    rows = [probe(choice.id, settings) for choice in MODELS]

    failures = 0
    for row in rows:
        ok = row["content"] and row["mode_tag"] and row["tools"]
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {row['model']}")
        print(f"      resolved={row['resolved']}  content={row['content']}  "
              f"mode_tag={row['mode_tag']}  tools={row['tools']}")
        if row["error"]:
            print(f"      error: {row['error']}")
        if row["content"] and not row["mode_tag"]:
            print("      ^ answers, but breaks MODE parsing — the groundedness "
                  "audit would silently never run for this model.")
    print(f"\n{len(rows) - failures}/{len(rows)} usable.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
