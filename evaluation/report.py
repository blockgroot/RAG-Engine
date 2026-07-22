"""Render golden-set results into a readable Markdown report.

The report is the human-facing output of the eval AND the input to the Part 3 gate
analysis (CLAUDE.md §Phase 7). It always includes the deterministic path-firing
table and a **gate-behaviour** section (per-case ``top_score`` vs threshold, and
whether the gate or the prompt did the refusing); it includes the RAGAS score table
only when RAGAS scoring was run.
"""

from __future__ import annotations

from .golden_set import GoldenCase
from .harness import CaseResult
from .ragas_scoring import METRIC_LABELS, RagasReport


def _fmt(x: float | None, nd: int = 3) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _gate_verdict(r: CaseResult, threshold: float) -> str:
    """Classify each case's interaction with the confidence gate."""
    ts = r.top_score
    if r.case.expected_source == "web":
        return "n/a (web path)"
    if ts is None:
        return "no retrieval (empty)"
    cleared = ts >= threshold
    if r.case.category in ("answerable", "conversation"):
        # Answerable: clearing the gate is correct; blocking would be a false negative.
        return "cleared gate (correct)" if cleared else "BLOCKED by gate (false negative)"
    # Fallback: if it cleared the gate, the prompt is what refused (gate let it through).
    return "cleared gate → prompt refused" if cleared else "blocked by gate (cheap filter)"


def build_report(
    results: list[CaseResult],
    threshold: float,
    ragas: RagasReport | None = None,
    generated_at: str | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# Golden-Set Evaluation Report")
    lines.append("")
    if generated_at:
        lines.append(f"_Generated: {generated_at}_")
        lines.append("")

    gating = [r for r in results if r.case.category != "web"]
    passed = sum(1 for r in gating if r.passed)
    lines.append(f"**Path-firing (gating): {passed}/{len(gating)} cases passed** "
                 f"(gate threshold = {threshold}). Web cases are advisory "
                 f"(network-dependent — see below).")
    lines.append("")

    # ---- 1. Path-firing table (deterministic, every-push tier) -----------
    lines.append("## 1. Path-firing checks (deterministic)")
    lines.append("")
    lines.append("Did the correct path fire, and are the expected facts present?")
    lines.append("")
    lines.append("| Case | Category | Expected | Actual | Facts | Rewrite | Verdict |")
    lines.append("|------|----------|----------|--------|-------|---------|---------|")
    for r in results:
        facts = {True: "✓", False: "✗", None: "—"}[r.facts_ok]
        rew = {True: "✓", False: "✗", None: "—"}[r.resolved_ok]
        if r.case.category == "web":
            if r.source == "web":
                verdict = "PASS (web fired)"
            elif r.source == "policy":
                verdict = "**FAIL (fabricated)**"
            else:
                verdict = "advisory (network)"
        else:
            verdict = "PASS" if r.passed else "**FAIL**"
        lines.append(
            f"| `{r.case.id}` | {r.case.category} | {r.case.expected_source} | "
            f"{r.source} | {facts} | {rew} | {verdict} |"
        )
    lines.append("")

    # ---- 2. Gate behaviour (input to Part 3) -----------------------------
    lines.append("## 2. Confidence-gate behaviour (per case)")
    lines.append("")
    lines.append(f"`top_score` is the best cosine similarity retrieved; the gate "
                 f"admits a question to the LLM only if `top_score ≥ {threshold}`.")
    lines.append("")
    lines.append("| Case | Category | top_score | Gate interaction |")
    lines.append("|------|----------|-----------|------------------|")
    for r in results:
        lines.append(
            f"| `{r.case.id}` | {r.case.category} | {_fmt(r.top_score)} | "
            f"{_gate_verdict(r, threshold)} |"
        )
    lines.append("")

    # ---- 3. RAGAS scores (expensive, less-frequent tier) -----------------
    lines.append("## 3. RAGAS scores (answerable cases)")
    lines.append("")
    if ragas is None:
        lines.append("_Not run in this invocation (deterministic/path-firing mode). "
                     "Run `python -m evaluation.run_eval --ragas` to score._")
        lines.append("")
    elif not ragas.per_case:
        lines.append("_No scorable cases produced grounded answers._")
        lines.append("")
    else:
        header = "| Case | " + " | ".join(METRIC_LABELS) + " |"
        sep = "|------|" + "|".join(["------"] * len(METRIC_LABELS)) + "|"
        lines.append(header)
        lines.append(sep)
        for cid, scores in ragas.per_case.items():
            row = " | ".join(_fmt(scores.get(m)) for m in METRIC_LABELS)
            lines.append(f"| `{cid}` | {row} |")
        mean_row = " | ".join(_fmt(ragas.means.get(m)) for m in METRIC_LABELS)
        base_row = " | ".join(_fmt(ragas.baseline.get(m)) for m in METRIC_LABELS)
        lines.append(f"| **mean** | {mean_row} |")
        lines.append(f"| _baseline_ | {base_row} |")
        lines.append("")
        if ragas.passed:
            lines.append("**RAGAS: all metric means ≥ baseline.** ✓")
        else:
            lines.append(f"**RAGAS: below baseline on {', '.join(ragas.failures)}.** ✗")
        lines.append("")

    # ---- 4. Per-case detail ---------------------------------------------
    lines.append("## 4. Per-case detail")
    lines.append("")
    for r in results:
        lines.append(f"### `{r.case.id}` ({r.case.category})")
        q = r.case.question
        lines.append(f"- **Question:** {q}")
        if r.resolved_question and r.resolved_question != q:
            lines.append(f"- **Rewritten to:** {r.resolved_question}")
        lines.append(f"- **Source / grounded:** {r.source} / {r.grounded}")
        lines.append(f"- **top_score:** {_fmt(r.top_score)}")
        ans = r.answer.replace("\n", " ").strip()
        lines.append(f"- **Answer:** {ans[:300]}{'…' if len(ans) > 300 else ''}")
        lines.append(f"- **Why in the set:** {r.case.rationale}")
        lines.append("")

    return "\n".join(lines)
