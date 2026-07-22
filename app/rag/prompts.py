"""Prompt construction for the grounded-generation step.

The prompt is layer 2 of the anti-hallucination defence, so it is explicit rather
than a casual "answer from the context" nudge. It forces the model to (1) answer
*only* from the supplied context, and (2) refuse with a fixed sentence whenever the
context doesn't directly answer — even when it's on a related topic (the failure
mode the similarity gate can't catch). The refusal sentence is passed in (from
``RagSettings.fallback_response``) so gate, prompt, and refusal detection share one
string. Full reasoning: CLAUDE.md §2/§4.
"""

from __future__ import annotations


def build_grounded_prompt(question: str, contexts: list[str], fallback_response: str) -> str:
    """Build the single grounded-answer prompt sent to the LLM.

    ``contexts`` are the retrieved chunk texts, most-relevant first. They are
    numbered so the model can cite them and so a human can trace the answer back
    to specific chunks.
    """
    numbered = "\n\n".join(f"[{i + 1}] {c.strip()}" for i, c in enumerate(contexts))

    return (
        "You are a company policy assistant. You answer strictly and only from "
        "the policy CONTEXT provided below.\n\n"
        "Follow these rules exactly:\n"
        "1. Use ONLY the information in the CONTEXT. Do not use outside knowledge, "
        "prior training, assumptions, or general world knowledge of any kind.\n"
        "2. If the CONTEXT does not directly and explicitly answer the QUESTION, "
        "you MUST reply with exactly this sentence and nothing else:\n"
        f"   {fallback_response}\n"
        "3. This rule holds EVEN IF the context is about a related, similar, or "
        "adjacent topic. Being on the same general subject is NOT enough — the "
        "context must directly answer the specific question asked. If it only "
        "touches a neighbouring topic, refuse using the exact sentence above.\n"
        "4. Never guess, infer beyond what is written, extrapolate, or fill gaps "
        "with what is 'probably' true. When refusing, return ONLY the exact "
        "sentence from rule 2 — no apology, no explanation, no extra text.\n"
        "5. When the context does answer the question, respond concisely and cite "
        "the context numbers you used in square brackets, e.g. [1] or [2].\n\n"
        f"CONTEXT:\n{numbered}\n\n"
        f"QUESTION: {question}\n\n"
        "ANSWER:"
    )


# --- Phase 5: conversation memory ------------------------------------------

def build_rewrite_prompt(question: str, summary: str | None, recent: list[tuple[str, str]]) -> str:
    """Build the cheap query-rewrite prompt (Capability A).

    Turns a context-dependent follow-up ("what about part-timers?") into a
    standalone, fully-resolved question suitable for embedding + retrieval. Its
    ONLY job is reference resolution — it must not answer anything.

    ``recent`` is a list of (question, answer) pairs, oldest first.
    """
    lines: list[str] = []
    if summary:
        lines.append(f"Summary of earlier conversation:\n{summary}")
    if recent:
        history = "\n".join(f"User: {q}\nAssistant: {a}" for q, a in recent)
        lines.append(f"Recent turns:\n{history}")
    context_block = "\n\n".join(lines) if lines else "(no prior context)"

    return (
        "You rewrite a user's latest question into a single STANDALONE question "
        "that can be understood on its own, resolving pronouns and references "
        "('that', 'it', 'they', 'what about X') using the conversation context.\n\n"
        "Rules:\n"
        "- Output ONLY the rewritten question: ONE line, ending with '?'.\n"
        "- Do NOT answer it, explain it, or add any other text.\n"
        "- If the latest question is already standalone, return it unchanged.\n"
        "- Preserve the user's intent; do not add facts not implied by context.\n\n"
        "Example:\n"
        "Recent turns:\n"
        "User: How many annual leave days do full-time employees get?\n"
        "Assistant: Full-time employees get 25 days.\n"
        "LATEST QUESTION: what about for part-timers?\n"
        "STANDALONE QUESTION: How many annual leave days do part-time employees get?\n\n"
        f"CONVERSATION CONTEXT:\n{context_block}\n\n"
        f"LATEST QUESTION: {question}\n\n"
        "STANDALONE QUESTION:"
    )


def build_summary_prompt(existing_summary: str | None, turns: list[tuple[str, str]]) -> str:
    """Build the prompt that compresses older turns into a running summary."""
    history = "\n".join(f"User: {q}\nAssistant: {a}" for q, a in turns)
    prior = f"EXISTING SUMMARY:\n{existing_summary}\n\n" if existing_summary else ""
    return (
        "You maintain a concise running summary of a conversation, so later "
        "follow-up questions can still be understood after older turns are "
        "dropped. Merge the existing summary (if any) with the new turns into a "
        "single short summary. Keep concrete facts the user may refer back to "
        "(names, numbers, entities, their situation). Omit pleasantries.\n\n"
        f"{prior}"
        f"NEW TURNS:\n{history}\n\n"
        "UPDATED SUMMARY:"
    )


# --- Phase 5: web-search fallback ------------------------------------------

# The tool schema offered to the model when internal retrieval fails the gate.
WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the public web for information about a REAL, NAMED, external "
            "entity — a specific company, product, service, insurance provider, "
            "law, or public organization — that would NOT be found in an internal "
            "company policy document. Only call this when the question is clearly "
            "about such a public, external, named thing. Do NOT call it for "
            "questions about the company's own internal policies, benefits, or "
            "procedures (e.g. 'our leave policy', 'do we offer X') — those are "
            "internal and should not trigger a web search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A concise web search query for the external entity.",
                }
            },
            "required": ["query"],
        },
    },
}


def build_web_decision_prompt(question: str, fallback_response: str) -> str:
    """System/user prompt for the single-step web-search decision.

    Internal retrieval already failed the confidence gate. The model must decide:
    is this about a public, external, named entity (→ call web_search), or is it
    internal-company info that simply isn't in our docs (→ do NOT search)?
    """
    return (
        "The company's internal policy documents did not contain an answer to the "
        "user's question. Decide what to do:\n"
        "- If the question is about a REAL, NAMED, EXTERNAL entity with plausible "
        "public information (a specific company, product, insurer, law, public "
        "service), call the web_search tool exactly once.\n"
        "- If the question is about the company's OWN internal policies/benefits/"
        "procedures that simply aren't in the docs, do NOT call any tool and "
        f"reply with exactly this sentence: {fallback_response}\n\n"
        f"QUESTION: {question}"
    )


def build_web_answer_prompt(question: str, results_block: str) -> str:
    """Prompt to compose the final answer from web results (single step)."""
    return (
        "Answer the user's QUESTION using the web SEARCH RESULTS below. Be "
        "concise and factual, and do not invent details beyond the results. If "
        "the results don't actually answer it, say you couldn't find a reliable "
        "answer.\n\n"
        f"SEARCH RESULTS:\n{results_block}\n\n"
        f"QUESTION: {question}\n\n"
        "ANSWER:"
    )
