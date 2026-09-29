"""Personal memory: the Second Brain's "who is asking" layer.

A handful of facts per person ("works in the Bangalore office", "prefers short
answers"), carried across chats so a new conversation does not start from
nothing. The rules are the whole design:

* **Written ONLY from the person's own questions.** Never from an answer: an
  answer can quote a document only they may read, and memory would carry that
  text past its access check into every later chat.
* **Never evidence.** Facts help interpret the question and set tone; the
  answer still comes only from documents, through the unchanged gate, strict
  prompt and audit (which is handed documents only, so a claim resting on a
  memory is unsupported by construction).
* **Saved automatically, never silently.** The chat shows "Remembered: ... ·
  Undo" the moment a fact is saved, and the account page lists and deletes all
  of them -- the production pattern (automatic + visible + undoable).
* **Private and bounded.** Keyed on ``(org_id, user_id)``; ``max_facts`` per
  person, oldest unpinned first out; a fact dies with its chat unless pinned.
  Off unless ``PERSONAL_MEMORY_ENABLED``, and a member or an org admin can
  switch it off. Slack and schedulers never read or write it.
* **Nothing sensitive.** Health, pay, credentials, family and the like are
  dropped even if the model proposes them.
"""

from __future__ import annotations

import contextvars
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from ..config.settings import PersonalMemorySettings
from ..db.connection import get_connection
from ..security.untrusted import (
    UNTRUSTED_POLICY,
    UNTRUSTED_REMINDER,
    normalize_untrusted,
    scrub_untrusted_text,
)

logger = logging.getLogger(__name__)

KINDS = ("preference", "context", "interest")
#: Facts saved from one question, at most.
MAX_PER_QUESTION = 3
MAX_FACT_CHARS = 120

# A question worth reading for memory talks about the asker. Most questions
# ("what is the leave policy?") do not, and pay nothing: no model call.
_SELF_TALK = re.compile(
    r"\b(i am|i'm|im|i work|i manage|i lead|i own|i prefer|i like|i usually|i always|"
    r"i joined|i report|my (team|role|manager|office|project|job)|we are|we're|"
    r"call me|remember|keep (it|answers|responses) (short|brief|detailed))\b",
    re.IGNORECASE,
)
_SENSITIVE = re.compile(
    r"\b(health|medical|diagnos\w*|illness|sick|pregnan\w*|disab\w*|therapy|"
    r"salary|salaries|pay|paid|compensation|bonus|debt|loan|bank|credit card|"
    r"password|passcode|pin|otp|token|secret|ssn|social security|passport|"
    r"religio\w*|politic\w*|sexual\w*|gender|ethnic\w*|race|divorce|lawsuit|"
    r"home address|phone number|family|child|children|kids|wife|husband|partner)\b",
    re.IGNORECASE,
)
_LINKISH = re.compile(r"https?://|www\.|@\w|<[^>]+>")
_LINE = re.compile(r"^\s*[-*]?\s*(preference|context|interest)\s*[:|]\s*(.+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Fact:
    id: str
    kind: str
    text: str
    pinned: bool
    created_at: datetime | None = None


# -- the facts the current request may use -----------------------------------

_ASKER: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "asker_facts", default=()
)


def use_asker_facts(facts: tuple[str, ...]) -> contextvars.Token:
    return _ASKER.set(tuple(facts))


def reset_asker_facts(token: contextvars.Token) -> None:
    _ASKER.reset(token)


def current_asker_facts() -> tuple[str, ...]:
    """Facts about the person asking THIS question; empty everywhere else."""
    return _ASKER.get()


# -- switches -----------------------------------------------------------------


def is_active(org_id: str, user_id: str | None,
              settings: PersonalMemorySettings | None = None) -> bool:
    """Deployment on, company on, person on. Any failure reads as off."""
    settings = settings or PersonalMemorySettings.from_env()
    if not settings.enabled or not user_id:
        return False
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT o.memory_enabled, u.memory_enabled
                FROM users u JOIN organizations o ON o.id = u.org_id
                WHERE u.id = %s::uuid AND o.id = %s::uuid
                """,
                (user_id, org_id),
            ).fetchone()
    except Exception:  # noqa: BLE001 - memory may only ever add
        logger.warning("personal memory switch lookup failed", exc_info=True)
        return False
    return bool(row and row[0] and row[1])


def switches(org_id: str, user_id: str) -> tuple[bool, bool]:
    """``(org_enabled, user_enabled)``."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT o.memory_enabled, u.memory_enabled
            FROM users u JOIN organizations o ON o.id = u.org_id
            WHERE u.id = %s::uuid AND o.id = %s::uuid
            """,
            (user_id, org_id),
        ).fetchone()
    return (bool(row[0]), bool(row[1])) if row else (False, False)


def set_user_enabled(org_id: str, user_id: str, enabled: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET memory_enabled = %s WHERE id = %s::uuid AND org_id = %s::uuid",
            (enabled, user_id, org_id),
        )


def set_org_enabled(org_id: str, enabled: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE organizations SET memory_enabled = %s WHERE id = %s::uuid",
            (enabled, org_id),
        )


# -- the store ----------------------------------------------------------------


def list_facts(org_id: str, user_id: str) -> list[Fact]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id::text, kind, text, pinned, created_at
            FROM user_memory
            WHERE org_id = %s::uuid AND user_id = %s::uuid
            ORDER BY pinned DESC, created_at DESC
            """,
            (org_id, user_id),
        ).fetchall()
    return [Fact(*r) for r in rows]


def save_facts(org_id: str, user_id: str, conversation_id: str | None,
               facts: list[tuple[str, str]],
               settings: PersonalMemorySettings | None = None) -> list[Fact]:
    """Insert new facts (duplicates ignored, case-insensitively), then trim the
    person's list to ``max_facts`` by dropping the oldest UNPINNED ones.
    Returns only the facts actually added -- the ones the chat announces."""
    settings = settings or PersonalMemorySettings.from_env()
    added: list[Fact] = []
    with get_connection() as conn:
        for kind, text in facts:
            row = conn.execute(
                """
                INSERT INTO user_memory (org_id, user_id, kind, text, source_conversation_id)
                VALUES (%s::uuid, %s::uuid, %s, %s, %s::uuid)
                ON CONFLICT (org_id, user_id, lower(text)) DO NOTHING
                RETURNING id::text, kind, text, pinned, created_at
                """,
                (org_id, user_id, kind, text, conversation_id),
            ).fetchone()
            if row:
                added.append(Fact(*row))
        conn.execute(
            """
            DELETE FROM user_memory WHERE id IN (
                SELECT id FROM user_memory
                WHERE org_id = %s::uuid AND user_id = %s::uuid AND NOT pinned
                ORDER BY created_at DESC
                OFFSET GREATEST(0, %s - (
                    SELECT count(*) FROM user_memory
                    WHERE org_id = %s::uuid AND user_id = %s::uuid AND pinned
                ))
            )
            """,
            (org_id, user_id, settings.max_facts, org_id, user_id),
        )
    return added


def delete_fact(org_id: str, user_id: str, fact_id: str) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM user_memory WHERE id = %s::uuid AND org_id = %s::uuid AND user_id = %s::uuid",
            (fact_id, org_id, user_id),
        )
        return bool(cur.rowcount)


def clear_facts(org_id: str, user_id: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM user_memory WHERE org_id = %s::uuid AND user_id = %s::uuid",
            (org_id, user_id),
        )
        return cur.rowcount or 0


def set_pinned(org_id: str, user_id: str, fact_id: str, pinned: bool) -> bool:
    """Pinning detaches a fact from its chat, so the 30-day chat purge cannot
    take it; unpinning leaves it detached (the chat may already be gone)."""
    with get_connection() as conn:
        cur = conn.execute(
            """
            UPDATE user_memory
            SET pinned = %s,
                source_conversation_id = CASE WHEN %s THEN NULL ELSE source_conversation_id END
            WHERE id = %s::uuid AND org_id = %s::uuid AND user_id = %s::uuid
            """,
            (pinned, pinned, fact_id, org_id, user_id),
        )
        return bool(cur.rowcount)


# -- extraction ---------------------------------------------------------------


def worth_reading(question: str) -> bool:
    """Cheap pre-filter: only a question that talks about the asker costs a call."""
    return bool(_SELF_TALK.search(question or ""))


def build_extract_prompt(question: str, known: tuple[str, ...]) -> str:
    known_block = "\n".join(f"- {k}" for k in known) or "(none)"
    return (
        "You maintain a short private memory about ONE employee, to help an "
        "assistant understand their future questions (which office, team or role "
        "they mean; how they like answers).\n\n"
        "From the employee's MESSAGE below, extract at most three NEW, lasting facts "
        "about the employee THEMSELVES, one per line, as:\n"
        "preference: <how they like answers>\n"
        "context: <their team, role, office, location, projects>\n"
        "interest: <a topic, team or project they keep asking about>\n"
        "Write each fact in the third person, under 15 words (e.g. 'Works in the "
        "Bangalore office'). Skip anything already in KNOWN FACTS, anything about "
        "other people, anything temporary, and anything sensitive (health, pay, "
        "family, credentials, beliefs, identity). If there is nothing, reply "
        "exactly: NONE\n\n"
        f"KNOWN FACTS:\n{known_block}\n\n"
        f"{UNTRUSTED_POLICY}\n"
        "MESSAGE:\n"
        "<<<UNTRUSTED_USER_MESSAGE>>>\n"
        f"{scrub_untrusted_text(question)}\n"
        "<<<END_UNTRUSTED_USER_MESSAGE>>>\n\n"
        f"{UNTRUSTED_REMINDER}\n\n"
        "FACTS:"
    )


def parse_facts(raw: str, known: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Validated ``(kind, text)`` pairs. Everything off-format is dropped: a
    fact is persisted text, so the bar is "certainly harmless", not "probably"."""
    known_low = {k.lower() for k in known}
    out: list[tuple[str, str]] = []
    for line in (raw or "").splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        kind = match.group(1).lower()
        text = normalize_untrusted(match.group(2)).strip().strip('"').rstrip(".").strip()
        if not text or len(text) > MAX_FACT_CHARS:
            continue
        if _SENSITIVE.search(text) or _LINKISH.search(text):
            continue
        if scrub_untrusted_text(text) != text:
            continue  # the scrubber would cut something: not a fact, an attack
        if text.lower() in known_low or any(text.lower() == t.lower() for _, t in out):
            continue
        out.append((kind, text))
        if len(out) >= MAX_PER_QUESTION:
            break
    return out


def remember_from_question(question: str, *, org_id: str, user_id: str,
                           conversation_id: str | None, known: tuple[str, ...],
                           llm=None) -> list[Fact]:
    """Read ONE question for lasting facts and save them. Never raises."""
    if not worth_reading(question):
        return []
    try:
        from ..llm.factory import build_aux_llm_provider
        from ..llm.metering import log_llm_call
        from ..llm.stages import STAGE_MEMORY_EXTRACT

        llm = llm or build_aux_llm_provider()
        raw = llm.generate(build_extract_prompt(question, known), max_tokens=120)
        log_llm_call(STAGE_MEMORY_EXTRACT, llm, org_id=org_id, conversation_id=conversation_id)
        facts = parse_facts(raw, known)
        if not facts:
            return []
        return save_facts(org_id, user_id, conversation_id, facts)
    except Exception:  # noqa: BLE001 - a fact not remembered, never a failed answer
        logger.warning("personal memory extraction failed", exc_info=True)
        return []
