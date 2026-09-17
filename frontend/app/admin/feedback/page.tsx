"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { FEEDBACK_REASONS, FeedbackSummary, api } from "@/lib/api";

const WINDOWS = [7, 30, 90] as const;

function reasonLabel(value: string | null) {
  return FEEDBACK_REASONS.find((r) => r.value === value)?.label ?? null;
}

function when(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/**
 * What people asked that this company could not answer.
 *
 * The list is not a report card on the assistant — it is a list of documents
 * nobody has written yet, which is why the refusals arrive here on their own
 * and nobody has to report them. "Asked by" counts DISTINCT people, because
 * one person re-asking five times is one person and the number that gets a
 * document written has to be true.
 */
export default function FeedbackPage() {
  const { me, loading } = useMe({ requireAdmin: true });
  const [days, setDays] = useState<number>(30);
  const [data, setData] = useState<FeedbackSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!me) return;
    setError(null);
    api
      .feedbackSummary(days)
      .then(setData)
      .catch(() => setError("Could not load feedback."));
  }, [me, days]);

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  const counts = data?.counts;

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Company"
          title="What we couldn’t answer"
          description="Questions people asked that came back without an answer, and answers someone marked wrong. Each line is a document worth writing or a source worth connecting."
          scene="people"
          meta={
            counts ? (
              <>
                <span className="studio-chip">{counts.refusals} unanswered</span>
                <span className="studio-chip studio-chip-ok">{counts.up} helpful</span>
                <span className="studio-chip">{counts.down} marked wrong</span>
              </>
            ) : null
          }
        />

        <div className="gap-window" role="group" aria-label="Time window">
          {WINDOWS.map((w) => (
            <button
              key={w}
              type="button"
              className="gap-window-option"
              data-active={days === w || undefined}
              onClick={() => setDays(w)}
            >
              Last {w} days
            </button>
          ))}
        </div>

        {error && (
          <div className="banner banner-warn" role="alert">
            {error}
          </div>
        )}

        <section className="studio-panel stack" aria-labelledby="gaps-title">
          <div className="studio-section-head">
            <h2 id="gaps-title">Unanswered questions</h2>
            <p className="muted">
              Most-asked first. A question with no score matched nothing at all — there is
              probably no document. A question with a score came close to something, so the
              document may exist and be hard to reach.
            </p>
          </div>

          {!data ? (
            <p className="muted">Loading…</p>
          ) : data.gaps.length === 0 ? (
            <p className="muted">
              Nothing went unanswered in this window. Questions land here on their own — there
              is nothing to switch on.
            </p>
          ) : (
            <table className="gap-table">
              <thead>
                <tr>
                  <th>Question</th>
                  <th>Asked by</th>
                  <th>Times</th>
                  <th>Closest match</th>
                  <th>Last asked</th>
                </tr>
              </thead>
              <tbody>
                {data.gaps.map((g) => (
                  <tr key={g.question + g.last_asked}>
                    <td>{g.question}</td>
                    <td>
                      {g.asked_by} {g.asked_by === 1 ? "person" : "people"}
                    </td>
                    <td>{g.refusals + g.downvotes}</td>
                    <td className="muted">
                      {g.best_gate_score === null
                        ? "nothing matched"
                        : g.best_gate_score.toFixed(2)}
                    </td>
                    <td className="muted">{when(g.last_asked)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="studio-panel stack" aria-labelledby="wrong-title">
          <div className="studio-section-head">
            <h2 id="wrong-title">Answers someone marked wrong</h2>
            <p className="muted">
              These came back confident. A gate cannot catch them, so a person saying so is
              the only signal there is.
            </p>
          </div>

          {!data ? (
            <p className="muted">Loading…</p>
          ) : data.downvoted.length === 0 ? (
            <p className="muted">Nobody marked an answer wrong in this window.</p>
          ) : (
            <ul className="gap-list">
              {data.downvoted.map((d) => (
                <li key={d.id} className="gap-item">
                  <p className="gap-item-question">{d.question}</p>
                  <p className="gap-item-meta muted">
                    {reasonLabel(d.reason) ?? "No reason given"}
                    {d.agent ? ` · ${d.agent}` : ""} · {when(d.created_at)}
                  </p>
                  {d.comment && <p className="gap-item-comment">“{d.comment}”</p>}
                  {d.answer && <p className="gap-item-answer muted">{d.answer}</p>}
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </AppShell>
  );
}
