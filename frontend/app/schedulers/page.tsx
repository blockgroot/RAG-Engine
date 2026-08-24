"use client";

import { useEffect, useRef, useState } from "react";
import { AnswerText } from "@/components/AnswerText";
import { AppShell } from "@/components/AppShell";
import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import {
  api,
  SchedulableConnection,
  SchedulerRecord,
  SetupChatMessage,
} from "@/lib/api";

const PROVIDER_LABEL: Record<string, string> = {
  github: "GitHub",
  slack: "Slack",
};

/** Only providers the backend can actually build a report from appear here. */
const PROVIDER_GLYPH: Record<string, BrandName> = {
  github: "github",
  slack: "slack",
};

const OPENING_LINE =
  "Tell me what you'd like to keep an eye on — which service, how often, and what the report should cover.";

/**
 * Human timestamp for a run. Inlined rather than imported: there is no shared
 * date helper in this app (ConnectionCard has its own copy too), and adding a
 * date library for two labels would be the wrong trade.
 */
function whenLabel(iso: string | null): string {
  if (!iso) return "—";
  const when = new Date(iso);
  const today = new Date();
  const sameDay = when.toDateString() === today.toDateString();
  const time = when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (sameDay) return `today at ${time}`;
  return `${when.toLocaleDateString([], { month: "short", day: "numeric" })} at ${time}`;
}

export default function SchedulersPage() {
  // Deliberately NOT requireAdmin: any member may schedule a report against a
  // connection the org already set up, and the API is member-level to match.
  const { me, loading } = useMe();
  const [schedulers, setSchedulers] = useState<SchedulerRecord[]>([]);
  const [connections, setConnections] = useState<SchedulableConnection[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [listMessage, setListMessage] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftPrompt, setDraftPrompt] = useState("");
  const [draftFrequency, setDraftFrequency] = useState("weekly");
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Setup conversation. Held here, not on the server: the endpoint is
  // stateless and this exchange is over in a couple of turns.
  const [messages, setMessages] = useState<SetupChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  function refresh() {
    api
      .listSchedulers()
      .then(setSchedulers)
      .catch((err) =>
        setListError(err instanceof Error ? err.message : "Could not load your reports.")
      );
  }

  function flash(ok: string | null, err: string | null = null) {
    if (clearTimer.current) clearTimeout(clearTimer.current);
    setListError(err);
    setListMessage(ok);
    if (ok) clearTimer.current = setTimeout(() => setListMessage(null), 4000);
  }

  useEffect(() => {
    if (me) {
      refresh();
      api.listSchedulableConnections().then(setConnections).catch(() => setConnections([]));
    }
    return () => {
      if (clearTimer.current) clearTimeout(clearTimer.current);
    };
  }, [me]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, thinking]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || thinking) return;

    const next: SetupChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    setDraft("");
    setChatError(null);
    setThinking(true);
    try {
      const result = await api.schedulerSetupChat(next);
      if (result.done && result.scheduler) {
        // Created: close the conversation out rather than leaving a dangling
        // exchange the user might keep adding to.
        setMessages([]);
        flash(
          `Scheduled a ${result.scheduler.frequency} ${
            PROVIDER_LABEL[result.scheduler.provider] ?? result.scheduler.provider
          } report — first one arrives ${whenLabel(result.scheduler.next_run_at)}.`
        );
        refresh();
      } else {
        setMessages([
          ...next,
          { role: "assistant", content: result.reply || "Could you say a bit more?" },
        ]);
      }
    } catch (err) {
      // Keep the user's message in the log — retyping it would be the wrong
      // punishment for a transient failure.
      setChatError(
        err instanceof Error ? err.message : "Could not reach the assistant just now."
      );
    } finally {
      setThinking(false);
    }
  }

  function startEdit(scheduler: SchedulerRecord) {
    setEditingId(scheduler.id);
    setDraftPrompt(scheduler.prompt);
    setDraftFrequency(scheduler.frequency);
  }

  async function saveEdit(scheduler: SchedulerRecord) {
    if (busyId) return;
    setBusyId(scheduler.id);
    try {
      await api.updateScheduler(scheduler.id, {
        prompt: draftPrompt.trim(),
        frequency: draftFrequency,
      });
      setEditingId(null);
      flash("Updated — the change applies from the next run.");
      refresh();
    } catch (err) {
      flash(null, err instanceof Error ? err.message : "Could not save that change.");
    } finally {
      setBusyId(null);
    }
  }

  async function remove(scheduler: SchedulerRecord) {
    if (busyId) return;
    if (!window.confirm("Delete this report? You will stop receiving it.")) return;
    setBusyId(scheduler.id);
    try {
      await api.deleteScheduler(scheduler.id);
      flash("Report deleted.");
      refresh();
    } catch (err) {
      flash(null, err instanceof Error ? err.message : "Could not delete that report.");
    } finally {
      setBusyId(null);
    }
  }

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  const nothingConnected = connections.length === 0;

  return (
    <AppShell me={me}>
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Explore"
          title="Scheduled reports"
          description="Ask a standing question about a connected service and get the answer emailed to you on a schedule. Your question is re-applied every run, so each report covers only what changed since the last one."
          meta={
            <>
              <span className="studio-chip">
                {schedulers.length} report{schedulers.length === 1 ? "" : "s"}
              </span>
              <span className="studio-chip">Delivered to {me.email}</span>
            </>
          }
        />

        <div className="people-layout">
          <section className="studio-panel" aria-labelledby="setup-title">
            <div className="studio-panel-glow" aria-hidden />
            <div className="studio-section-head">
              <h2 id="setup-title">New report</h2>
              <p className="muted">
                {nothingConnected
                  ? "Nothing schedulable is connected yet."
                  : "Describe it in your own words — no forms."}
              </p>
            </div>

            {nothingConnected ? (
              <div className="banner banner-wait" role="status">
                Reports can currently read GitHub and Slack. Ask an admin to connect one
                on the Sources page, then come back.
              </div>
            ) : (
              <>
                <div className="chat-log" style={{ minHeight: "12rem" }}>
                  <div className="chat-bubble chat-bubble-assistant">
                    <AnswerText text={OPENING_LINE} />
                  </div>
                  {messages.map((message, i) =>
                    message.role === "user" ? (
                      <div key={i} className="chat-bubble chat-bubble-user">
                        {message.content}
                      </div>
                    ) : (
                      <div key={i} className="chat-bubble chat-bubble-assistant">
                        <AnswerText text={message.content} />
                      </div>
                    )
                  )}
                  {thinking && (
                    <div className="chat-bubble chat-bubble-assistant" data-thinking>
                      <div className="chat-thinking" role="status" aria-live="polite">
                        <span className="chat-thinking-dots" aria-hidden>
                          <span />
                          <span />
                          <span />
                        </span>
                        <span className="chat-thinking-label">Thinking…</span>
                      </div>
                    </div>
                  )}
                  <div ref={bottomRef} />
                </div>

                {chatError && (
                  <div className="banner banner-warn" role="alert">
                    {chatError}
                  </div>
                )}

                <form onSubmit={send} className="chat-composer">
                  <input
                    className="chat-composer-input"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    placeholder="e.g. weekly Slack summary of what shipped"
                    aria-label="Describe the report you want"
                    disabled={thinking}
                  />
                  <button
                    className="chat-composer-send"
                    type="submit"
                    disabled={thinking || !draft.trim()}
                    aria-label="Send"
                  >
                    →
                  </button>
                </form>

                <p className="muted" style={{ marginTop: "0.5rem", fontSize: "0.8rem" }}>
                  Available now:{" "}
                  {connections.map((c) => PROVIDER_LABEL[c.provider] ?? c.provider).join(", ")}
                </p>
              </>
            )}
          </section>

          <section className="roster-board" aria-labelledby="reports-title">
            <div className="studio-section-head roster-board-head">
              <h2 id="reports-title">Your reports</h2>
              <p className="muted">Only yours — nobody else in the company sees these.</p>
            </div>

            {listError && (
              <div className="banner banner-warn" role="alert" style={{ marginBottom: "0.75rem" }}>
                {listError}
              </div>
            )}
            {listMessage && (
              <div className="banner banner-ok" role="status" style={{ marginBottom: "0.75rem" }}>
                {listMessage}
              </div>
            )}

            <div className="roster-scroll">
              {schedulers.length === 0 ? (
                <div className="studio-empty">
                  <div className="studio-empty-mark" aria-hidden />
                  <h3>No reports yet</h3>
                  <p className="muted">
                    Describe your first one on the left and it will show up here.
                  </p>
                </div>
              ) : (
                <ul className="people-grid">
                  {schedulers.map((scheduler, i) => {
                    const label =
                      PROVIDER_LABEL[scheduler.provider] ?? scheduler.provider;
                    const glyph = PROVIDER_GLYPH[scheduler.provider];
                    const editing = editingId === scheduler.id;
                    const stopped = scheduler.status === "failed";
                    return (
                      <li
                        key={scheduler.id}
                        className="people-card"
                        style={{ animationDelay: `${0.08 + i * 0.05}s` }}
                      >
                        {glyph ? (
                          <span className="people-avatar" aria-hidden>
                            <BrandGlyph name={glyph} size={22} />
                          </span>
                        ) : (
                          <span className="people-avatar" aria-hidden>
                            {label.charAt(0)}
                          </span>
                        )}

                        <div className="people-card-copy">
                          {editing ? (
                            <>
                              <div className="field">
                                <label htmlFor={`prompt-${scheduler.id}`}>
                                  What should it cover?
                                </label>
                                <input
                                  id={`prompt-${scheduler.id}`}
                                  className="input"
                                  value={draftPrompt}
                                  onChange={(e) => setDraftPrompt(e.target.value)}
                                  disabled={busyId === scheduler.id}
                                />
                              </div>
                              <div className="field">
                                <label htmlFor={`freq-${scheduler.id}`}>How often?</label>
                                <select
                                  id={`freq-${scheduler.id}`}
                                  className="input"
                                  value={draftFrequency}
                                  onChange={(e) => setDraftFrequency(e.target.value)}
                                  disabled={busyId === scheduler.id}
                                >
                                  <option value="weekly">Weekly</option>
                                  <option value="monthly">Monthly</option>
                                </select>
                              </div>
                            </>
                          ) : (
                            <>
                              <strong>{scheduler.prompt}</strong>
                              <span className="muted">
                                {label} · {scheduler.frequency} · next{" "}
                                {whenLabel(scheduler.next_run_at)}
                                {scheduler.last_run_at
                                  ? ` · last sent ${whenLabel(scheduler.last_run_at)}`
                                  : " · not sent yet"}
                              </span>
                              {scheduler.last_error && (
                                <span className="muted">
                                  {stopped
                                    ? "Stopped after repeated failures: "
                                    : "Last run failed, will retry: "}
                                  {scheduler.last_error}
                                </span>
                              )}
                            </>
                          )}
                        </div>

                        <div className="people-card-meta">
                          <span
                            className={`studio-chip ${
                              stopped ? "studio-chip-warn" : "studio-chip-ok"
                            }`}
                          >
                            {stopped ? "stopped" : "active"}
                          </span>
                          <div className="people-card-actions">
                            {editing ? (
                              <>
                                <button
                                  type="button"
                                  className="button"
                                  disabled={busyId === scheduler.id || !draftPrompt.trim()}
                                  onClick={() => saveEdit(scheduler)}
                                >
                                  {busyId === scheduler.id ? "…" : "Save"}
                                </button>
                                <button
                                  type="button"
                                  className="button button-secondary"
                                  disabled={busyId === scheduler.id}
                                  onClick={() => setEditingId(null)}
                                >
                                  Cancel
                                </button>
                              </>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  className="button button-secondary"
                                  disabled={busyId === scheduler.id}
                                  onClick={() => startEdit(scheduler)}
                                >
                                  Edit
                                </button>
                                <button
                                  type="button"
                                  className="button button-secondary"
                                  disabled={busyId === scheduler.id}
                                  onClick={() => remove(scheduler)}
                                >
                                  Delete
                                </button>
                              </>
                            )}
                          </div>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </section>
        </div>
      </main>
    </AppShell>
  );
}
