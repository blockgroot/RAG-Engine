"""Prompt construction for the grounded-generation step.

The prompt is the primary defence against hallucination, so it is written to be
deliberate and explicit rather than a casual "answer from the context" nudge.
Its whole job is to make the model do two things reliably:

1. Answer *only* from the supplied context — never from the model's own world
   knowledge.
2. Refuse — with a fixed, verbatim sentence — whenever the context does not
   *directly* answer the question, **even if the context is about a related or
   adjacent topic**. Topical overlap is explicitly called out as insufficient,
   because that is the exact failure mode a similarity threshold cannot catch
   (on-topic chunks score above the gate) and where a lax prompt would let the
   model reason its way to a plausible but ungrounded answer.

The refusal sentence is passed in (from ``RagSettings.fallback_response``) so the
gate, the prompt, and the pipeline's refusal detection all use one string.
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
