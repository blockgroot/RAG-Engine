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

  const attention = topics?.filter((t) => t.alerting).length ?? 0;

  return (
    <section className="room-section gap-section" aria-labelledby="gap-topics-title">
      <p className="gap-rule muted" id="gap-topics-title">
        A topic shows here when, in the last 14 days, 3 different people couldn&rsquo;t get an answer or
        2 marked its answers wrong. You get a notification when one appears.
      </p>

      {error && (
        <p className="act-card-error" role="alert">
          {error}
        </p>
      )}

      {!topics ? (
        !error && <p className="muted">Loading…</p>
      ) : topics.length === 0 ? (
        <div className="gap-clear">
          <span className="gap-clear-mark" aria-hidden>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="m5 12 5 5 9-10" />
            </svg>
          </span>
          <div>
            <b>Nothing to fix right now</b>
            <p className="muted">No topic has reached that point in the last 14 days.</p>
          </div>
        </div>
      ) : (
        <>
          <p className="hero-facts gap-summary">
            <b>{topics.length}</b> topic{topics.length === 1 ? "" : "s"}
            {attention > 0 && (
              <>
                <span aria-hidden>·</span>
                <span className="hero-fact-warn">{attention} need{attention === 1 ? "s" : ""} attention</span>
              </>
            )}
          </p>
          <ul className="gap-list">
            {topics.map((t, i) => (
              <li
                key={t.id}
                id={`topic-${t.id}`}
                className={`gap-item${t.alerting ? " is-alerting" : ""}`}
                style={{ ["--i" as string]: i }}
              >
                <details
                  open={opened === t.id}
                  onToggle={(e) => {
                    const isOpen = (e.target as HTMLDetailsElement).open;
                    setOpened((cur) => (isOpen ? t.id : cur === t.id ? null : cur));
                  }}
                >
                  <summary>
                    <span className="gap-count" aria-hidden>
                      {/* One count, never the two added: the same person can be in both. */}
                      <b>{t.missing_people || t.wrong_people}</b>
                      <small>{t.missing_people ? "no answer" : "marked wrong"}</small>
                    </span>
                    <span className="gap-head">
                      <span className="gap-item-question">&ldquo;{t.label}&rdquo;</span>
                      <span className="gap-item-meta muted">
                        {t.missing_people > 0 && `${people(t.missing_people)} got no answer`}
                        {t.missing_people > 0 && t.wrong_people > 0 && " · "}
                        {t.wrong_people > 0 && `${people(t.wrong_people)} marked an answer wrong`}
                        {" · "}last asked {when(t.last_asked)}
                        {t.surfaces.includes("slack") && " · asked in Slack too"}
                      </span>
                    </span>
                    {t.alerting && <span className="gap-flag">Needs attention</span>}
                    <span className="gap-chevron" aria-hidden>›</span>
                  </summary>

                  <div className="gap-body">
                    <p className={`gap-hint ${t.best_score === null ? "is-missing" : "is-weak"}`}>
                      {t.best_score === null
                        ? "Nothing in your connected sources came close, so the document is probably missing."
                        : "Something related exists but wasn't a close enough match. It may be hard to find, or only partly cover this."}
                    </p>
                    <p className="gap-label">How people asked it</p>
                    <ul className="gap-asks">
                      {t.questions.map((q) => (
                        <li key={q}>{q}</li>
                      ))}
                    </ul>
                    {t.wrong_answer && (
                      <>
                        <p className="gap-label">
                          The latest answer marked wrong
                          {t.wrong_reason &&
                            ` · ${FEEDBACK_REASONS.find((r) => r.value === t.wrong_reason)?.label ?? t.wrong_reason}`}
                        </p>
                        <blockquote className="gap-wrong">
                          {t.wrong_answer}
                          {t.wrong_comment && <cite>&ldquo;{t.wrong_comment}&rdquo;</cite>}
                        </blockquote>
                      </>
                    )}
                    <div className="gap-actions">
                      <button
                        type="button"
                        className="button button-secondary"
                        disabled={busy === t.id}
                        onClick={() => void handled(t.id)}
                      >
                        {busy === t.id ? "Saving…" : "Mark as handled"}
                      </button>
                      <span className="muted">Hidden until it&rsquo;s asked again.</span>
                    </div>
                  </div>
                </details>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
