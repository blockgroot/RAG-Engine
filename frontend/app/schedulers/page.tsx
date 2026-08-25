"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { api, SchedulerRecord, SchedulerSpace } from "@/lib/api";

const PROVIDER_LABEL: Record<string, string> = {
  github: "GitHub",
  slack: "Slack",
  linear: "Linear",
};

/**
 * Glyphs for the providers a report can read. Kept as a lookup rather than a
 * switch so adding a source is one line here — the same reason
 * ConnectionCard is table-driven. The backend decides what is *offered*
 * (`GET /schedulers/connections`); an unmapped provider still renders, just
 * with an initial instead of a mark.
 */
const PROVIDER_GLYPH: Record<string, BrandName> = {
  github: "github",
  slack: "slack",
  linear: "linear",
};

/**
 * "GitHub, Slack or Linear" — derived, not written out, so this copy cannot
 * drift the next time a source is added (it already had, once).
 */
const SCHEDULABLE_LABELS = (() => {
  const names = Object.values(PROVIDER_LABEL);
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
})();

/**
 * Labels for the sources a space can have connected, schedulable or not —
 * this is what the chip after a space name shows ("Meeting notes · Drive"),
 * so it needs Notion and Drive too.
 */
const SOURCE_LABEL: Record<string, string> = {
  ...PROVIDER_LABEL,
  notion: "Notion",
};

/** "Company", or the space name plus what it has connected. */
function spaceLabel(space: SchedulerSpace): string {
  if (space.scope === "org") return space.name;
  const sources = space.connected.map((p) => SOURCE_LABEL[p] ?? p).join(", ");
  return sources ? `${space.name} · ${sources}` : `${space.name} · nothing connected`;
}

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
  const [spaces, setSpaces] = useState<SchedulerSpace[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [listMessage, setListMessage] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftPrompt, setDraftPrompt] = useState("");
  const [draftFrequency, setDraftFrequency] = useState("weekly");
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // New-report form. Three explicit slots (space, service, cadence) plus the
  // free-text intent. Deterministic on purpose: the space and service decide
  // which connection is read, and that is not a thing to infer from prose.
  // "" = nothing picked yet; "org" = the company-wide connection.
  const [spaceKey, setSpaceKey] = useState("");
  const [provider, setProvider] = useState("");
  const [frequency, setFrequency] = useState("weekly");
  const [prompt, setPrompt] = useState("");
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const selectedSpace = useMemo(
    () => spaces.find((s) => (s.id ?? "org") === spaceKey) ?? null,
    [spaces, spaceKey]
  );

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
      api
        .listSchedulableConnections()
        .then((r) => setSpaces(r.spaces))
        .catch(() => setSpaces([]));
    }
    return () => {
      if (clearTimer.current) clearTimeout(clearTimer.current);
    };
  }, [me]);

  // Changing the space invalidates the chosen service: a space sees only its
  // own connections, so carrying the old pick over could submit a provider
  // this space never connected.
  useEffect(() => {
    setProvider("");
  }, [spaceKey]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (creating || !selectedSpace || !provider || !prompt.trim()) return;
    setCreating(true);
    setFormError(null);
    try {
      const created = await api.createScheduler(
        provider,
        frequency,
        prompt.trim(),
        selectedSpace.id
      );
      setPrompt("");
      flash(
        `Scheduled a ${created.frequency} ${
          PROVIDER_LABEL[created.provider] ?? created.provider
        } report${
          created.workspace_name ? ` for ${created.workspace_name}` : ""
        } — first one arrives ${whenLabel(created.next_run_at)}.`
      );
      refresh();
    } catch (err) {
      setFormError(
        err instanceof Error ? err.message : "Could not create that report."
      );
    } finally {
      setCreating(false);
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

  // Every schedulable provider across every scope this member can reach.
  const nothingConnected =
    spaces.length > 0 && spaces.every((space) => space.providers.length === 0);

  return (
    <AppShell me={me}>
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Explore"
          title="Scheduled reports"
          description="Ask a standing question about a connected service and get the answer emailed to you on a schedule. Your question is re-applied every run, so each report covers only what changed since the last one."
          scene="reports"
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
                  : "Pick where to read from, then say what you want to know."}
              </p>
            </div>

            {nothingConnected ? (
              <div className="banner banner-wait" role="status">
                Reports can currently read {SCHEDULABLE_LABELS}. Ask an admin to connect
                one on the Sources page, or connect one inside a space, then come back.
              </div>
            ) : (
              <form onSubmit={create} className="stack">
                <div className="field">
                  <label htmlFor="space">1 · Which space?</label>
                  <select
                    id="space"
                    className="input"
                    value={spaceKey}
                    onChange={(e) => setSpaceKey(e.target.value)}
                    disabled={creating}
                  >
                    <option value="">Select a space…</option>
                    {spaces.map((space) => (
                      <option key={space.id ?? "org"} value={space.id ?? "org"}>
                        {spaceLabel(space)}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="field">
                  <label htmlFor="provider">2 · Which service?</label>
                  <select
                    id="provider"
                    className="input"
                    value={provider}
                    onChange={(e) => setProvider(e.target.value)}
                    disabled={creating || !selectedSpace || !selectedSpace.providers.length}
                  >
                    <option value="">
                      {selectedSpace ? "Select a service…" : "Pick a space first"}
                    </option>
                    {(selectedSpace?.providers ?? []).map((name) => (
                      <option key={name} value={name}>
                        {PROVIDER_LABEL[name] ?? name}
                      </option>
                    ))}
                  </select>
                  {/* Disclosed, not hidden: a space whose only sources have no
                      "what happened since T" feed would otherwise look broken. */}
                  {selectedSpace && !selectedSpace.providers.length && (
                    <p className="muted" style={{ fontSize: "0.8rem" }}>
                      {selectedSpace.connected.length
                        ? `${selectedSpace.name} has ${selectedSpace.connected
                            .map((c) => SOURCE_LABEL[c] ?? c)
                            .join(", ")} connected, which cannot be scheduled yet. Reports need a service with an activity feed: ${SCHEDULABLE_LABELS}.`
                        : `${selectedSpace.name} has nothing connected yet.`}
                    </p>
                  )}
                </div>

                <div className="field">
                  <label htmlFor="prompt">3 · What should the report cover?</label>
                  <div className="scheduler-compose">
                    <input
                      id="prompt"
                      className="input"
                      value={prompt}
                      onChange={(e) => setPrompt(e.target.value)}
                      placeholder="e.g. summarise what the team discussed and flag anything urgent"
                      disabled={creating}
                    />
                    <select
                      className="input"
                      value={frequency}
                      onChange={(e) => setFrequency(e.target.value)}
                      aria-label="How often?"
                      disabled={creating}
                    >
                      <option value="weekly">Weekly</option>
                      <option value="monthly">Monthly</option>
                    </select>
                    <button
                      className="button"
                      type="submit"
                      disabled={creating || !selectedSpace || !provider || !prompt.trim()}
                    >
                      {creating ? "Creating…" : "Create"}
                    </button>
                  </div>
                </div>

                {formError && (
                  <div className="banner banner-warn" role="alert">
                    {formError}
                  </div>
                )}
              </form>
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
                                {label}
                                {/* Which scope it reads: two reports on the
                                    same service in different spaces are
                                    otherwise indistinguishable. */}
                                {scheduler.workspace_name
                                  ? ` in ${scheduler.workspace_name}`
                                  : " · company-wide"}{" "}
                                · {scheduler.frequency} · next{" "}
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
