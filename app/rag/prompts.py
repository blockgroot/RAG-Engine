"""Prompt construction for the grounded-generation step.

The prompt is layer 2 of the anti-hallucination defence, so it is explicit rather
than a casual "answer from the context" nudge. It forces the model to (1) answer
*only* from the supplied context, and (2) refuse with a fixed sentence whenever
there is no supporting evidence. When context is related but not explicit, the
model may report what the documents actually say while clearly distinguishing
that from an explicit answer — never inventing unsupported conclusions.
The refusal sentence is passed in (from ``RagSettings.fallback_response``) so
gate, prompt, and refusal detection share one string. Full reasoning: CLAUDE.md.
"""

from __future__ import annotations


def build_grounded_prompt(question: str, contexts: list[str], fallback_response: str) -> str:
    """Build the single grounded-answer prompt sent to the LLM.

    ``contexts`` are the retrieved chunk texts, most-relevant first. They are
    numbered so the model can cite them and so a human can trace the answer back
    to specific chunks.

    Three response modes only:
    1. Explicitly Supported — context directly answers; answer + citations.
    2. Related but Not Explicit — report what docs say; state they do not
       explicitly answer; no unsupported conclusions.
    3. No Supporting Evidence — exact ``fallback_response`` only.
    """
    numbered = "\n\n".join(f"[{i + 1}] {c.strip()}" for i, c in enumerate(contexts))

    return (
        "You are a company policy assistant. You answer strictly and only from "
        "the policy CONTEXT provided below.\n\n"
        "Follow these rules exactly:\n"
        "1. Use ONLY the information in the CONTEXT. Do not use outside knowledge, "
        "prior training, assumptions, or general world knowledge of any kind.\n"
        "2. Choose exactly one of these three response modes:\n"
        "   A. Explicitly Supported — the CONTEXT directly and explicitly answers "
        "the QUESTION. Answer concisely from the CONTEXT and cite the context "
        "numbers you used in square brackets, e.g. [1] or [2].\n"
        "   B. Related but Not Explicit — the CONTEXT is about a related topic but "
        "does NOT explicitly answer the QUESTION. Report what the documents "
        "actually say (with citations). Clearly state that the documents do not "
        "explicitly answer the question. Do NOT invent a yes/no conclusion, legal "
        "interpretation, or any claim that is not written in the CONTEXT.\n"
        "   C. No Supporting Evidence — the CONTEXT is irrelevant or empty of "
        "useful related information. Reply with exactly this sentence and nothing "
        f"else:\n   {fallback_response}\n"
        "3. Never guess, infer beyond what is written, extrapolate, or fill gaps "
        "with what is 'probably' true. Every claim must be supported by the "
        "CONTEXT. Unsupported conclusions are forbidden in every mode.\n"
        "4. When using mode C, return ONLY the exact sentence from rule 2C — no "
        "apology, no explanation, no extra text.\n\n"
        f"CONTEXT:\n{numbered}\n\n"
        f"QUESTION: {question}\n\n"
        "ANSWER:"
    )


def build_recovery_queries_prompt(question: str, hit_snippets: list[str]) -> str:
    """Build the retrieval-recovery prompt (Retrieval Discovery Gap).

    Produces alternative *retrieval-oriented search expressions* that preserve
    the user's intent. This must NOT answer the question. Expressions may include
    synonyms, abbreviations, spelling corrections, document terminology, alternate
    phrasings, and related vocabulary — only to help retrieval discover better
    evidence.
    """
    if hit_snippets:
        snippets = "\n".join(
            f"- {s.strip()[:240]}" for s in hit_snippets if s and s.strip()
        )
        evidence_block = f"CURRENT TOP RETRIEVED SNIPPETS (may be weak or off):\n{snippets}"
    else:
        evidence_block = "CURRENT TOP RETRIEVED SNIPPETS: (none)"

    return (
        "You help a document-search system recover from a Retrieval Discovery Gap.\n"
        "The user's question may use different vocabulary than the documents "
        "(synonyms, abbreviations, typos, alternate phrasing, related terms).\n\n"
        "Your ONLY job: propose alternative retrieval-oriented search expressions "
        "that preserve the user's original intent and may help find the right "
        "passages. You must NOT answer the question, change the intent, or invent "
        "facts.\n\n"
        "Rules:\n"
        "- Output ONE search expression per line, nothing else.\n"
        "- Do not number lines or add commentary.\n"
        "- Preserve the user's intent; never replace it with a different question.\n"
        "- Expressions may include: synonyms, abbreviations, spelling corrections, "
        "document terminology, alternate phrasings, related vocabulary.\n"
        "- Prefer short search-like phrases over full sentences.\n\n"
        f"USER QUESTION (intent to preserve):\n{question}\n\n"
        f"{evidence_block}\n\n"
        "RETRIEVAL EXPRESSIONS:"
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
            "law, public organization, news event, or campaign — that would NOT be "
            "fully answered by an internal company policy document. Only call this "
            "when the question is clearly about such a public, external, named "
            "thing. Do NOT call it for questions about the company's own internal "
            "policies, benefits, or procedures (e.g. 'our leave policy', 'do we "
            "offer X') — those are internal and should not trigger a web search."
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

    Called when internal evidence is insufficient (gate miss, or generation
    refused after retrieve/recovery). The model must decide: public external
    named entity (→ call web_search), or internal-company info (→ do NOT search)?
    """
    return (
        "The company's internal policy documents did not answer the user's "
        "question (either nothing relevant was found, or retrieved passages were "
        "related but did not explicitly answer it). Decide what to do:\n"
        "- If the question is about a REAL, NAMED, EXTERNAL entity with plausible "
        "public information (a specific company, product, insurer, law, public "
        "organization, news event, or campaign), call the web_search tool exactly "
        "once.\n"
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
