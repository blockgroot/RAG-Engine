"""The golden set: a small, deliberate regression suite for the Policy Agent.

WHY THIS SHAPE
--------------
The set is written against the **real ingested Notion test data** — the "Acme HR
Policies" page used to verify Phases 4–6 (see CLAUDE.md §6). Its facts (25 days
leave, ZephyrCare Platinum, part-time 12 days, …) are reproduced here verbatim as
``CORPUS`` so the evaluation is **self-contained and deterministic**: it seeds its
own org and cleans it up (exactly like ``tests/test_grounding.py``), so it runs
identically on a laptop and in CI, which cannot reach Notion (no token, no shared
page). The questions and expected facts are the same ones a real user asks of that
page — we only decouple the eval from the live Notion dependency, not from the data.

WHY THESE CASES (kept to ~17, not exhaustive — a regression tripwire, not a
benchmark)
-------------
Four categories, one per path the agent can take, so a regression in *any* path
fails loudly:

- ``answerable`` (10) — facts clearly present in the docs. These exercise the
  happy path AND are the only cases scored by RAGAS (faithfulness / answer
  relevancy / context precision / context recall need a real grounded answer +
  reference). They span single-fact lookups (leave days, sick days, holidays),
  a named entity (ZephyrCare Platinum), a procedural rule (2 weeks via HR portal),
  and the full-time/part-time distinction that hybrid retrieval must disambiguate.
- ``fallback`` (4) — nothing in the docs answers them, so the agent MUST return the
  fixed internal fallback. Includes the deliberate hard case ``parental-leave``:
  it is *topically adjacent* to the leave policies (so retrieval scores it above the
  gate threshold), which means the strict prompt — not the gate — has to refuse. It
  is the single most important case for the Part 3 gate analysis.
- ``web`` (2) — a real, named, EXTERNAL entity not in the docs, which should trip
  the web-search tool (``source == "web"``), not a hallucinated policy answer.
  (Network-dependent: DuckDuckGo rate-limits, so these are gated in the scheduled
  eval, not the every-push fast tier — see evaluation/README.md.)
- ``conversation`` (1) — a follow-up that is meaningless alone ("what about
  part-timers?"), proving memory-driven query rewriting + retrieval of the *right*
  (part-time, not full-time) chunk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Corpus — verbatim facts from the real "Acme HR Policies" Notion page, split
# into documents so retrieval genuinely has to pick the right chunk (e.g.
# full-time vs part-time leave). Ingesting this reproduces the same content the
# live Notion pipeline produced for Phases 4–6.
# --------------------------------------------------------------------------
CORPUS: list[tuple[str, str]] = [
    (
        "Annual Leave — Full-Time",
        """
# Annual Leave — Full-Time Employees
Full-time employees are entitled to 25 days of paid annual leave per year,
accrued monthly. Annual leave must be requested at least two weeks in advance
through the HR portal. Up to 5 unused annual leave days may be carried over into
the next calendar year.
""",
    ),
    (
        "Annual Leave — Part-Time",
        """
# Annual Leave — Part-Time Employees
Part-time employees receive 12 days of paid annual leave per year, pro-rated by
the number of hours they work each week.
""",
    ),
    (
        "Public Holidays",
        """
# Public Holidays
Employees receive 11 paid public holidays each year.
""",
    ),
    (
        "Sick Leave",
        """
# Sick Leave
Sick leave is 10 days per year, separate from annual leave. A doctor's note is
required for sick absences longer than three consecutive days.
""",
    ),
    (
        "Health and Dental",
        """
# Health and Dental Cover
Health and dental cover is provided through the ZephyrCare Platinum group plan.
""",
    ),
    (
        "Remote Work",
        """
# Remote Work
Employees may work remotely up to 3 days per week with manager approval.
""",
    ),
    (
        "Expense Reimbursement",
        """
# Expense Reimbursement
Business travel expenses are reimbursed up to $500 per trip. Original receipts
must be submitted within 30 days of the expense.
""",
    ),
    (
        "Facilities",
        """
# Facilities
The office kitchen is cleaned every Friday afternoon. Visitor parking is
available in Lot C near the main entrance.
""",
    ),
]


@dataclass(frozen=True)
class GoldenCase:
    """One golden-set case.

    - ``id``               stable identifier (used in the report and CI output).
    - ``category``         ``answerable`` | ``fallback`` | ``web`` | ``conversation``.
    - ``question``         the (final) question asked. For ``conversation`` it is
      the follow-up; ``prior_turns`` holds the turns asked before it.
    - ``prior_turns``      earlier turns to establish context (``conversation`` only).
    - ``expected_source``  the path that MUST fire: ``policy`` | ``none`` | ``web``.
    - ``expected_facts``   substrings a correct answer must contain (answerable /
      conversation). Case-insensitive check.
    - ``reference_answer`` ground-truth answer, used by RAGAS for the answerable
      cases (context recall / answer relevancy compare against it).
    - ``resolved_contains``substrings the rewritten standalone question must contain
      (``conversation`` only) — proves memory rewriting happened.
    - ``rationale``        why this case is in the set.
    """

    id: str
    category: str
    question: str
    expected_source: str
    prior_turns: list[str] = field(default_factory=list)
    expected_facts: list[str] = field(default_factory=list)
    reference_answer: str | None = None
    resolved_contains: list[str] = field(default_factory=list)
    rationale: str = ""


GOLDEN_CASES: list[GoldenCase] = [
    # ---- answerable (RAGAS-scored) ---------------------------------------
    GoldenCase(
        id="annual-leave-days",
        category="answerable",
        question="How many days of paid annual leave do full-time employees get per year?",
        expected_source="policy",
        expected_facts=["25"],
        reference_answer="Full-time employees are entitled to 25 days of paid annual leave per year, accrued monthly.",
        rationale="Core single-fact lookup; the most common real question.",
    ),
    GoldenCase(
        id="carry-over",
        category="answerable",
        question="How many unused annual leave days can be carried over into the next year?",
        expected_source="policy",
        expected_facts=["5"],
        reference_answer="Up to 5 unused annual leave days may be carried over into the next calendar year.",
        rationale="Detail within the same section; tests precision, not just topic match.",
    ),
    GoldenCase(
        id="leave-notice",
        category="answerable",
        question="How far in advance must annual leave be requested, and how?",
        expected_source="policy",
        expected_facts=["two weeks", "HR portal"],
        reference_answer="Annual leave must be requested at least two weeks in advance through the HR portal.",
        rationale="A procedural rule (not a number); checks the model reports the how, not just the how-many.",
    ),
    GoldenCase(
        id="part-time-leave",
        category="answerable",
        question="How much paid annual leave do part-time employees receive?",
        expected_source="policy",
        expected_facts=["12"],
        reference_answer="Part-time employees receive 12 days of paid annual leave per year, pro-rated by the hours they work.",
        rationale="Must retrieve the PART-TIME chunk, not the full-time one — the disambiguation hybrid retrieval targets.",
    ),
    GoldenCase(
        id="public-holidays",
        category="answerable",
        question="How many paid public holidays do employees get each year?",
        expected_source="policy",
        expected_facts=["11"],
        reference_answer="Employees receive 11 paid public holidays each year.",
        rationale="Distinct short doc; guards against holidays being confused with leave days.",
    ),
    GoldenCase(
        id="sick-leave-days",
        category="answerable",
        question="How many paid sick days do employees get per year?",
        expected_source="policy",
        expected_facts=["10"],
        reference_answer="Employees get 10 paid sick days per year, separate from annual leave.",
        rationale="Separate leave type; checks it is not conflated with annual leave.",
    ),
    GoldenCase(
        id="sick-note",
        category="answerable",
        question="When is a doctor's note required for sick leave?",
        expected_source="policy",
        expected_facts=["three", "consecutive"],
        reference_answer="A doctor's note is required for sick absences longer than three consecutive days.",
        rationale="Conditional rule; tests faithful reporting of the exact condition.",
    ),
    GoldenCase(
        id="health-plan",
        category="answerable",
        question="What health and dental insurance plan does the company provide?",
        expected_source="policy",
        expected_facts=["ZephyrCare"],
        reference_answer="Health and dental cover is provided through the ZephyrCare Platinum group plan.",
        rationale="A named entity that IS in the docs — must be answered from policy (contrast with the web cases).",
    ),
    GoldenCase(
        id="remote-work",
        category="answerable",
        question="How many days per week can employees work remotely?",
        expected_source="policy",
        expected_facts=["3"],
        reference_answer="Employees may work remotely up to 3 days per week with manager approval.",
        rationale="Non-leave policy; broadens topic coverage beyond leave.",
    ),
    GoldenCase(
        id="expense-limit",
        category="answerable",
        question="What is the reimbursement limit for business travel expenses per trip?",
        expected_source="policy",
        expected_facts=["500"],
        reference_answer="Business travel expenses are reimbursed up to $500 per trip, with receipts submitted within 30 days.",
        rationale="Monetary fact; another distinct topic.",
    ),
    # ---- fallback (no relevant info -> fixed internal fallback) ----------
    GoldenCase(
        id="gym-discount",
        category="fallback",
        question="What discount do employees get at the on-site company gym?",
        expected_source="none",
        rationale="Clearly unrelated to any stored content — the gate should filter this cheaply.",
    ),
    GoldenCase(
        id="parental-leave",
        category="fallback",
        question="What is the company's parental and maternity leave policy, and how many weeks are paid?",
        expected_source="none",
        rationale=(
            "THE HARD CASE. Topically adjacent to the leave docs, so retrieval scores it "
            "above the gate threshold; the strict PROMPT (not the gate) must refuse. Central "
            "to the Part 3 gate analysis."
        ),
    ),
    GoldenCase(
        id="401k-match",
        category="fallback",
        question="Does the company match 401(k) retirement contributions, and by how much?",
        expected_source="none",
        rationale="Plausible HR topic with no coverage in the docs; must not be invented.",
    ),
    GoldenCase(
        id="dress-code",
        category="fallback",
        question="What is the dress code for client-facing meetings?",
        expected_source="none",
        rationale="Another plausible-but-absent policy; guards against confident hallucination.",
    ),
    # ---- web (real external named entity -> web-search tool) -------------
    GoldenCase(
        id="cigna-coverage",
        category="web",
        question="What does Cigna health insurance generally cover?",
        expected_source="web",
        rationale=(
            "A real EXTERNAL insurer not in our docs. Should trip the web-search tool, not a "
            "policy answer (contrast with health-plan, whose ZephyrCare IS internal). "
            "Network-dependent — gated in the scheduled eval only."
        ),
    ),
    GoldenCase(
        id="bamboohr-what-is",
        category="web",
        question="What is BambooHR and what is it typically used for?",
        expected_source="web",
        rationale="A real external product; second web case so a single flaky search does not decide the path check.",
    ),
    # ---- conversation (memory-driven follow-up) --------------------------
    GoldenCase(
        id="followup-part-timers",
        category="conversation",
        prior_turns=[
            "How many days of paid annual leave do full-time employees get per year?"
        ],
        question="what about for part-timers?",
        expected_source="policy",
        expected_facts=["12"],
        reference_answer="Part-time employees receive 12 days of paid annual leave per year, pro-rated by hours worked.",
        resolved_contains=["part"],
        rationale=(
            "A follow-up meaningless on its own. Proves the memory rewrite turns it into a "
            "standalone part-time question and retrieves the part-time (not full-time) chunk."
        ),
    ),
]


def cases_by_category(category: str) -> list[GoldenCase]:
    return [c for c in GOLDEN_CASES if c.category == category]
