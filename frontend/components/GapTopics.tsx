"use client";

/**
 * What people couldn't get answered, grouped by meaning, for whoever can fix
 * it: company-wide topics for an admin, a space's for its owner.
 *
 * The bell links here with `#topic-<id>`, so that topic opens and scrolls
 * into view. Counts only, never who asked. "Mark as handled" hides a topic
 * until new people hit it.
 */

import { useEffect, useState } from "react";

import { ApiError, FEEDBACK_REASONS, api, type GapTopic } from "@/lib/api";

function when(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function people(n: number) {
  return `${n} ${n === 1 ? "person" : "people"}`;
}

export function GapTopics({ workspaceId }: { workspaceId?: string | null }) {
  const [topics, setTopics] = useState<GapTopic[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [opened, setOpened] = useState<string | null>(null);

  useEffect(() => {
    api
      .gapTopics(workspaceId)
      .then(({ topics }) => setTopics(topics))
      .catch(() => setError("Could not load these."));
  }, [workspaceId]);

  // The bell's link names one topic: open it and bring it into view.
  useEffect(() => {
    if (!topics) return;
    const open = () => {
      const id = window.location.hash.replace(/^#topic-/, "");
      if (!id || !topics.some((t) => t.id === id)) return;
      setOpened(id);
      requestAnimationFrame(() =>
        document.getElementById(`topic-${id}`)?.scrollIntoView({ block: "start" }),
      );
    };
    open();
    // The bell links here from this same page too, which changes only the hash.
    window.addEventListener("hashchange", open);
    return () => window.removeEventListener("hashchange", open);
  }, [topics]);

  async function handled(id: string) {
    setBusy(id);
    setError(null);
    try {
      await api.markTopicHandled(id, workspaceId);
      setTopics((prev) => prev?.filter((t) => t.id !== id) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not mark it handled.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="studio-panel stack" aria-labelledby="gap-topics-title">
      <div className="studio-section-head">
        <h2 id="gap-topics-title">What people couldn&rsquo;t get answered</h2>
        <p className="muted">
          Questions from the last 14 days, grouped by what they ask about. You get a notification
          when 3 different people can&rsquo;t get an answer on one topic, or 2 mark its answers
          wrong.
        </p>
      </div>

      {error && (
        <p className="act-card-error" role="alert">
          {error}
        </p>
      )}

      {!topics ? (
        !error && <p className="muted">Loading…</p>
      ) : topics.length === 0 ? (
        <p className="muted">Nothing went unanswered in the last 14 days.</p>
      ) : (
        <ul className="gap-list">
          {topics.map((t) => (
            <li key={t.id} id={`topic-${t.id}`} className="gap-item">
              <details
                open={opened === t.id}
                onToggle={(e) => {
                  const isOpen = (e.target as HTMLDetailsElement).open;
                  setOpened((cur) => (isOpen ? t.id : cur === t.id ? null : cur));
                }}
              >
                <summary>
                  <span className="gap-item-question">&ldquo;{t.label}&rdquo;</span>{" "}
                  {t.alerting && <span className="studio-chip">Needs attention</span>}
                  <p className="gap-item-meta muted">
                    {t.missing_people > 0 && `${people(t.missing_people)} got no answer`}
                    {t.missing_people > 0 && t.wrong_people > 0 && " · "}
                    {t.wrong_people > 0 && `${people(t.wrong_people)} marked an answer wrong`}
                    {" · "}last asked {when(t.last_asked)}
                    {t.surfaces.includes("slack") && " · asked in Slack too"}
                  </p>
                </summary>

                <p className="gap-item-meta muted">
                  {t.best_score === null
                    ? "Nothing in your connected sources came close, so the document is probably missing."
                    : "Something related exists but wasn't a close enough match. It may be hard to find, or only partly cover this."}
                </p>
                <p className="gap-item-meta">How people asked it:</p>
                <ul>
                  {t.questions.map((q) => (
                    <li key={q} className="gap-item-comment">
                      {q}
                    </li>
                  ))}
                </ul>
                {t.wrong_answer && (
                  <>
                    <p className="gap-item-meta">
                      The latest answer marked wrong
                      {t.wrong_reason &&
                        ` (${FEEDBACK_REASONS.find((r) => r.value === t.wrong_reason)?.label ?? t.wrong_reason})`}
                      :
                    </p>
                    <p className="gap-item-answer muted">{t.wrong_answer}</p>
                    {t.wrong_comment && <p className="gap-item-comment">&ldquo;{t.wrong_comment}&rdquo;</p>}
                  </>
                )}
                <div className="act-card-actions">
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={busy === t.id}
                    onClick={() => void handled(t.id)}
                  >
                    {busy === t.id ? "Saving…" : "Mark as handled"}
                  </button>
                  <span className="muted act-card-timer">
                    Hidden until it&rsquo;s asked again.
                  </span>
                </div>
              </details>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
