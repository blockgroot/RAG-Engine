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
