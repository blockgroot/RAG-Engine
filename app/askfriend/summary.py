"""The summary a colleague receives: what the asker asked, what Handbook found
or could not find, then the question.

The conversation is OUTSIDE text here: earlier answers repeat documents, so it
is fenced under the shared policy. The question line is appended by code, so
the colleague always sees the asker's exact words.
"""

from __future__ import annotations

from ..core.exceptions import LLMProviderError
from ..llm.base import LLMProvider
from ..memory.conversations import ConversationTurnRow
from ..rag.cite import strip_citations
from ..security.links import enforce_link_provenance
from ..security.untrusted import UNTRUSTED_POLICY, UNTRUSTED_REMINDER, scrub_untrusted_text

#: The latest turns read; older ones are named as left out, never silently dropped.
MAX_TURNS = 12
#: One answer's share of the prompt.
MAX_ANSWER_CHARS = 2000
#: The whole reply: up to ~12 short lines plus any preamble a model writes.
_MAX_TOKENS = 1500


def build_summary_prompt(
    turns: list[tuple[str, str]], question: str, *, left_out: int, asker_name: str = "The person"
) -> str:
    """The prompt over ``(question, answer)`` pairs, oldest first."""
    history = "\n\n".join(f"Q: {q}\nA: {a}" for q, a in turns)
    if left_out:
        history = f"({left_out} earlier turns were left out.)\n\n{history}"
    fenced = (
        "<<<UNTRUSTED_CONVERSATION_CONTENT>>>\n"
        f"{scrub_untrusted_text(history)}\n"
        "<<<END_UNTRUSTED_CONVERSATION_CONTENT>>>"
    )
    return (
        f"{asker_name} asked Handbook, their company's assistant, a question it could not "
        "answer, and is forwarding it to a colleague who may know. Write the note the "
        f"colleague will read, on their behalf. Refer to them as {asker_name} or \"they\", "
        "never he or she: a name does not tell you anyone's pronouns. Do not open with a "
        "greeting or say who is writing; start with what they wanted to know.\n\n"
        "Summarize clearly what the person was trying to find out and what Handbook "
        "found or could not find along the way, so the colleague can answer without "
        "asking back. Scale the length to the conversation: 3 to 5 sentences for a short "
        "one, up to about 12 short lines or bullets for a long one. Use only what the "
        "conversation says; never add facts. Plain text, no headings, no "
        "sign-off, and do not restate the final question (it is added after your note).\n\n"
        f"{UNTRUSTED_POLICY}\n"
        f"{fenced}\n\n"
        f"{UNTRUSTED_REMINDER}\n\n"
        "NOTE:"
    )


def summarize(
    turns: list[ConversationTurnRow], question: str, llm: LLMProvider, *, asker_name: str = "The person"
) -> str:
    """The note plus ``Question: <question>``. Raises ``LLMProviderError`` on an empty reply."""
    kept = turns[-MAX_TURNS:]
    pairs = [(t.question, (t.answer or "")[:MAX_ANSWER_CHARS]) for t in kept]
    prompt = build_summary_prompt(pairs, question, left_out=len(turns) - len(kept), asker_name=asker_name)
    reply = llm.generate(prompt, max_tokens=_MAX_TOKENS)
    seen = "\n".join(f"{q}\n{a}" for q, a in pairs)
    note = enforce_link_provenance(strip_citations(reply or ""), [seen], ()).strip()
    if not note:
        raise LLMProviderError("The model wrote no summary")
    return f"{note}\n\nQuestion: {question}"
