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


# --- Phase 10: query understanding + expansion (pre-retrieval) -------------

def build_query_understanding_prompt(question: str, max_expansions: int) -> str:
    """Build the single combined normalize+expand prompt (Capability, Phase 10).

    ONE LLM call does two jobs at once (avoiding a second round trip): (1)
    normalize the question for retrieval (fix typos/grammar/abbreviations,
    preserve intent exactly), and (2) propose alternate retrieval-oriented
    phrasings that ADD document-style vocabulary alongside the user's own words.

    This stage is VOCABULARY EXPANSION, never semantic interpretation: it must
    never answer, summarize, or explain the question, never infer what a policy
    says, and — critically — never replace a specific term (a named mechanism,
    form, product, policy, or abbreviation) with only a broader category. E.g.
    "carry forward leave" must not become "annual leave entitlement" alone,
    since that drops the specific carry-forward mechanism the user asked about
    and could retrieve the wrong (merely same-category) chunk. Every generated
    query must either keep the original specific term or add broader vocabulary
    ALONGSIDE it — never as a silent replacement.
    """
    return (
        "You improve a user's question for DOCUMENT RETRIEVAL ONLY. This is "
        "VOCABULARY EXPANSION, not semantic interpretation — you do not answer "
        "the question, you do not summarize or explain it, you do not infer "
        "what any policy says or means (you have not seen any document — you "
        "are only guessing at wording, never at meaning), and you must never "
        "invent facts, numbers, or entities not implied by the question itself. "
        "Your only job is to help a search system find the right passages.\n\n"
        "Do two things:\n"
        "1. NORMALIZE the question: fix spelling mistakes and typos, expand "
        "abbreviations, fix grammar, and phrase it as a single clear question in "
        "the same language — while preserving the user's exact intent. Do not "
        "narrow, broaden, summarize, or answer it.\n"
        f"2. EXPAND: propose up to {max_expansions} short alternate search "
        "phrases that ADD plausible document-style vocabulary ALONGSIDE the "
        "user's own words — a policy document may use more formal or generic "
        "terms than the user's casual phrasing. These are retrieval queries, "
        "not restatements of the question, and not answers.\n\n"
        "CRITICAL — preserve specific terminology, never discard it:\n"
        "- If the question names a SPECIFIC mechanism, form, product, policy "
        "name, benefit name, abbreviation, or other domain-specific term, that "
        "term must survive in at least one expansion — either unchanged, or "
        "paired with additional vocabulary. NEVER produce expansions that ALL "
        "replace it with only a broader category term.\n"
        "- Bad: question mentions 'carry forward leave' -> expansions "
        "['annual leave entitlement', 'leave policy'] (the specific "
        "carry-forward mechanism is gone from every expansion — this changes "
        "intent, not just wording, and could retrieve the wrong chunk).\n"
        "- Good: question mentions 'carry forward leave' -> expansions "
        "['leave carry forward policy', 'unused leave carried over to next "
        "year', 'annual leave carry-forward rules'] (each keeps the specific "
        "carry-forward concept while varying surrounding wording).\n"
        "- Broader category terms may be ADDED as extra options alongside "
        "term-preserving ones, never as the only option.\n\n"
        "Example (illustrative — do not assume this domain unless the question "
        "implies it):\n"
        'question: "can i get protein supplements reimbursed?"\n'
        '{"normalized": "Can I get protein supplements reimbursed?", '
        '"expansions": ["protein supplement reimbursement policy", '
        '"dietary supplement coverage under health allowance", '
        '"permissible health-related expenses"]}\n'
        "(the first two expansions keep \"protein\"/\"supplement\" while adding "
        "policy-style wording; only the third drops to a pure category term, "
        "and it's offered ALONGSIDE the specific-term variants, not instead of "
        "them.)\n\n"
        "Rules:\n"
        "- Never answer, summarize, or explain the question.\n"
        "- Never infer or state what a policy says — you have not seen any "
        "document content.\n"
        "- Never invent information, facts, or entities not implied by the "
        "question itself.\n"
        "- Never let an expansion silently drop a specific named term in the "
        "question — preserve it in at least one expansion, per the rule above.\n"
        "- Work generically for ANY domain/topic — do not assume a specific "
        "category (e.g. do not assume it's about health, finance, leave, or "
        "travel unless the question itself says so). The examples above "
        "illustrate the STYLE of transformation, not the topic.\n"
        "- Output ONLY a single JSON object, no markdown code fences, no "
        "commentary, in exactly this shape:\n"
        '{"normalized": "<normalized question>", "expansions": ["<phrase>", ...]}\n\n'
        f"QUESTION: {question}\n\n"
        "JSON:"
    )


# --- Phase 10: evidence classification + graded grounded generation --------

# The four evidence-support levels the classified prompt distinguishes.
EVIDENCE_CLASSIFICATIONS = ("explicit", "implicit", "partial", "none")


def build_classified_grounded_prompt(
    question: str, contexts: list[str], fallback_response: str
) -> str:
    """Build the combined classification + graded-answer prompt (Phase 10).

    Replaces the old binary "answer or refuse" prompt. ONE LLM call both (1)
    classifies how well the CONTEXT supports the QUESTION — explicit / implicit /
    partial / none — using ONLY the retrieved evidence (no outside knowledge),
    and (2) produces an answer whose style matches that classification. Folding
    classification into the same call as generation avoids a second LLM round
    trip. The output format is deliberately rigid (CLASSIFICATION:/ANSWER: lines)
    so the pipeline can parse it reliably; ``RagPipeline._parse_classified_response``
    falls back gracefully to treating the whole reply as a plain answer if a model
    doesn't follow the format (so this also works with simple fakes in tests).
    """
    numbered = "\n\n".join(f"[{i + 1}] {c.strip()}" for i, c in enumerate(contexts))

    return (
        "You are a company policy assistant. Answer using ONLY the policy "
        "CONTEXT below — never outside knowledge, prior training, or "
        "assumptions of any kind.\n\n"
        "STEP 1 — Classify how well the CONTEXT addresses the QUESTION. Choose "
        "exactly ONE of these four labels:\n"
        "- explicit: the context directly and explicitly states the answer.\n"
        "- implicit: the context does not explicitly state the answer, but "
        "clearly implies or lets you reasonably infer it from what IS stated "
        "(e.g. a general rule, a defined scope, or closely related wording that "
        "covers this case without naming it outright).\n"
        "- partial: the context answers PART of the question but is missing "
        "some of what was asked.\n"
        "- none: the context is genuinely unrelated to the question, or only "
        "shares a broad topic/category with nothing that actually bears on the "
        "specific thing asked. Being in the same general subject area is NOT "
        "enough by itself — the content must have some real bearing on the "
        "specific question.\n\n"
        "IMPORTANT — multi-part questions: if the QUESTION has more than one "
        "part (e.g. 'X, and what about Y', 'is A true, and what else is there'), "
        "classify based on the BEST-supported part. Only use 'none' if the "
        "context bears on NONE of the parts. If at least one part is explicit "
        "and another is unclear/uncertain, that is 'partial' (or 'implicit'), "
        "NEVER 'none' — 'none' is reserved for when nothing in the question is "
        "addressed at all.\n\n"
        "STEP 2 — Respond according to your classification:\n"
        "- explicit: answer confidently and concisely, citing context numbers "
        "in square brackets, e.g. [1].\n"
        "- implicit: give the best answer you can reasonably infer, but "
        "EXPLICITLY say the policy does not state this directly, explain which "
        "context you are inferring from and why it is relevant, and cite it. "
        "Clearly separate what is inferred from what is explicitly written — "
        "never present an inference as if it were a direct policy statement.\n"
        "- partial: clearly state what the context DOES tell the user, and "
        "explicitly say what part of the question it does NOT cover. Do not "
        "guess at the missing part.\n"
        "- none: respond with EXACTLY this sentence and nothing else — no "
        "apology, no explanation, no extra text:\n"
        f"   {fallback_response}\n\n"
        "Rules that apply to EVERY classification:\n"
        "- Never invent facts, numbers, names, or policies not present in the "
        "CONTEXT.\n"
        "- Never let 'implicit' or 'partial' become a guess dressed up as a "
        "fact — always be explicit about what is stated vs. inferred.\n"
        "- Never guess, extrapolate, or fill gaps with what is 'probably' true "
        "beyond a reasonable, clearly-labelled inference in the implicit case.\n\n"
        "Output ONLY plain text in exactly this format, nothing before or "
        "after, no markdown code fences:\n"
        "CLASSIFICATION: <explicit|implicit|partial|none>\n"
        "ANSWER: <your response following the rules above>\n\n"
        f"CONTEXT:\n{numbered}\n\n"
        f"QUESTION: {question}\n\n"
        "OUTPUT:"
    )


def build_stricter_regeneration_prompt(
    question: str,
    contexts: list[str],
    fallback_response: str,
    previous_answer: str,
    unsupported_sentences: list[str],
) -> str:
    """Build the ONE stricter-regeneration prompt used after a failed verification.

    Only fires when the deterministic verifier (Phase 10, ``app/verification/``)
    flags at least one sentence of the drafted answer as unsupported by the
    retrieved evidence. Shown the specific flagged sentences so the model can
    remove/fix exactly those, rather than a vague "be more careful" nudge. Uses
    the SAME classification/output format so the caller can parse it identically;
    if the model still cannot produce a fully-supported answer it should honestly
    classify as ``none``.
    """
    numbered = "\n\n".join(f"[{i + 1}] {c.strip()}" for i, c in enumerate(contexts))
    flagged = "\n".join(f"- {s}" for s in unsupported_sentences)

    return (
        "You previously drafted an answer to a policy question, but some of its "
        "sentences could NOT be verified as supported by the retrieved CONTEXT. "
        "Produce a corrected answer using ONLY what the CONTEXT actually "
        "supports.\n\n"
        f"PREVIOUS DRAFT:\n{previous_answer}\n\n"
        f"SENTENCES THAT COULD NOT BE VERIFIED:\n{flagged}\n\n"
        "Rewrite the answer so every statement is directly supported by, or a "
        "clearly-labelled reasonable inference from, the CONTEXT below. Remove "
        "or fix anything that was not actually supported. If, once you remove "
        "the unsupported parts, nothing meaningful remains, classify as 'none' "
        "and use the exact fallback sentence.\n\n"
        "Follow the same rules as before:\n"
        "- explicit: confident answer from context stated directly, cite [n].\n"
        "- implicit: clearly-labelled inference from related context, cite [n].\n"
        "- partial: state what IS supported; say what is missing; no guessing.\n"
        "- none: respond with EXACTLY this sentence and nothing else:\n"
        f"   {fallback_response}\n\n"
        "Output ONLY plain text in exactly this format:\n"
        "CLASSIFICATION: <explicit|implicit|partial|none>\n"
        "ANSWER: <corrected response>\n\n"
        f"CONTEXT:\n{numbered}\n\n"
        f"QUESTION: {question}\n\n"
        "OUTPUT:"
    )
