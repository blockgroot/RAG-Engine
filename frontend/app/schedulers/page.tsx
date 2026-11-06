"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { SpaceMark } from "@/components/SpaceMark";
import { useMe } from "@/lib/useMe";
import {
  api,
  ReportRow,
  SchedulableConnection,
  SchedulerRecord,
  SchedulerSpace,
} from "@/lib/api";

/** Cadence labels. A table because the two inline ternaries this replaces both
 *  read "weekly or else monthly", which silently mislabelled daily. */
const FREQUENCY_LABEL: Record<string, string> = {
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly",
};

const PROVIDER_LABEL: Record<string, string> = {
  github: "GitHub",
  slack: "Slack",
  linear: "Linear",
  notion: "Notion",
  // Drive's provider string is "google" — what the connect flow stores. Keying
  // this "google_drive" would render a raw "google" and match nothing.
  google: "Google Drive",
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
  notion: "notion",
  google: "drive",
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
 * Labels for the sources a space can have connected. Every provider is now
 * schedulable, so this is just PROVIDER_LABEL — kept as its own name because
 * it answers a different question (what a space HAS, in the chip after its
 * name) and the two would diverge again the moment a non-schedulable source
 * is added.
 */
const SOURCE_LABEL: Record<string, string> = { ...PROVIDER_LABEL };

/**
 * Report intents built from what a connection actually covers, so the prompt
 * field starts from a real channel or repo rather than a blank box. One
 * template per topic name, plus a scope-wide one that needs no topic — Linear
 * has no stored subset to name, so it only ever gets the latter.
 *
 * These are starting points a person then edits: the field stays free text,
 * because the whole feature is a standing question in the user's own words.
 */
function promptSuggestions(provider: string, topics: string[]): string[] {
  const perTopic: Record<string, (t: string) => string> = {
    slack: (t) => `Summarise what was discussed in #${t.replace(/^#/, "")} and flag anything urgent`,
    github: (t) => `Summarise the commits in ${t} and call out anything risky`,
  };
  const scopeWide: Record<string, string> = {
    slack: "Summarise the week's discussions and flag anything that needs a decision",
    github: "Summarise what was merged and call out anything risky",
    linear: "What shipped, what moved, and what is stuck waiting on someone",
  };

  const template = perTopic[provider];
  const suggestions = template ? topics.map(template) : [];
  const wide = scopeWide[provider];
  if (wide) suggestions.push(wide);
  return suggestions;
}

function Logos({ providers }: { providers: string[] }) {
  return (
    <span className="space-logos">
      {providers.map((p, i) =>
        PROVIDER_GLYPH[p] ? (
          <i key={p} title={SOURCE_LABEL[p] ?? p} style={{ ["--i" as string]: i }}>
            <BrandGlyph name={PROVIDER_GLYPH[p]} size={12} />
          </i>
        ) : null,
      )}
    </span>
  );
}

/**
 * Where a report reads from: the company's apps, or one space's. A menu of
 * rows with each scope's logos, in place of a native select that could show
 * only text. Labelled by which APPS get read, never by audience: a schedule
 * is personal and only its creator is emailed (`schedulers/runner.py`).
 */
function ScopePicker({
  value,
  onChange,
  org,
  spaces,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  org: SchedulerSpace | null;
  spaces: SchedulerSpace[];
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const picked = value === "org" ? org : spaces.find((s) => s.id === value) ?? null;
  const pick = (v: string) => {
    onChange(v);
    setOpen(false);
  };
  const row = (v: string, space: SchedulerSpace, mark: React.ReactNode, sub: string) => (
    <button
      key={v}
      type="button"
      role="option"
      aria-selected={value === v}
      className="scope-row"
      onClick={() => pick(v)}
    >
      {mark}
      <span className="scope-row-copy">
        <b>{space.scope === "org" ? "Company-wide" : space.name}</b>
        <small>{sub}</small>
      </span>
      <Logos providers={space.connected.length ? space.connected : space.providers} />
    </button>
  );
  return (
    <div
      className="scope-picker"
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false);
      }}
      onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
    >
      <button
        type="button"
        id="scope"
        className="reports-scope"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
      >
        <span>Reads from</span>
        <b>{picked ? (picked.scope === "org" ? "Company-wide" : picked.name) : "Choose…"}</b>
        {picked && <Logos providers={picked.providers} />}
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
          <path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <div className="scope-menu" role="listbox" aria-label="Where should this read from?">
          {org && (
            <>
              <p className="scope-menu-label">The company&rsquo;s apps</p>
              {row(
                "org",
                org,
                <span className="scope-org-mark" aria-hidden>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M4 20V6l8-3 8 3v14M9 20v-4h6v4M8 9h.01M12 9h.01M16 9h.01M8 13h.01M12 13h.01M16 13h.01" />
                  </svg>
                </span>,
                "Apps connected for the whole company",
              )}
            </>
          )}
          {spaces.length > 0 && (
            <>
              <p className="scope-menu-label">One space&rsquo;s apps</p>
              {spaces.map((space) =>
                row(
                  space.id ?? "",
                  space,
                  <SpaceMark name={space.name} className="scope-space-mark" />,
                  space.connected.length ? "Only this space's own apps" : "Nothing connected yet",
                ),
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Human timestamp for a run. Inlined rather than imported: there is no shared
 * date helper in this app (ConnectionCard has its own copy too), and adding a
 * date library for two labels would be the wrong trade.
 */
/** The next run as a date block for the timeline: "13 Nov" over "5:06 pm". */
function runDate(iso: string | null): [string, string] {
  if (!iso) return ["—", ""];
  const when = new Date(iso);
  return [
    when.toLocaleDateString([], { day: "numeric", month: "short" }),
    when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: true }),
  ];
}

function whenLabel(iso: string | null): string {
  if (!iso) return "—";
  const when = new Date(iso);
  const today = new Date();
  const sameDay = when.toDateString() === today.toDateString();
  // hour12 forced rather than left to the locale: the browser's locale decides
  // 12h vs 24h otherwise, so an en-GB reader saw "15:02" while en-US saw
  // "3:02 PM" on the same page. ConnectionCard already pins it the same way.
  const time = when.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
  if (sameDay) return `today at ${time}`;
  return `${when.toLocaleDateString([], { month: "short", day: "numeric" })} at ${time}`;
}

export default function SchedulersPage() {
  // Deliberately NOT requireAdmin: any member may schedule a report against a
  // connection the org already set up, and the API is member-level to match.
  const { me, loading } = useMe();
  const [schedulers, setSchedulers] = useState<SchedulerRecord[]>([]);
  const [spaces, setSpaces] = useState<SchedulerSpace[]>([]);
  const [connections, setConnections] = useState<SchedulableConnection[]>([]);
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [listMessage, setListMessage] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftPrompt, setDraftPrompt] = useState("");
  const [draftFrequency, setDraftFrequency] = useState("weekly");
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // New-report form: WHERE to read from, WHAT to cover, HOW OFTEN. The
  // service is no longer asked — it is classified from the prompt server-side
  // (`_classify_provider`), because "which app holds this?" is our data model
  // showing through, not a question the reader has.
  //
  // One picker, not the old scope-then-which-one pair. Two steps made the
  // distinction *worse*: "Organisation" and "Personal space" were abstract
  // category names, and the real one ("Company" vs a space you can name) only
  // appeared after a second choice. An <optgroup> states both at once.
  // "" = nothing picked yet, "org" = company-wide, otherwise a workspace id.
  const [scope, setScope] = useState("");
  const [frequency, setFrequency] = useState("weekly");
  const [prompt, setPrompt] = useState("");
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const orgSpace = useMemo(() => spaces.find((s) => s.scope === "org") ?? null, [spaces]);
  const mySpaces = useMemo(() => spaces.filter((s) => s.scope === "workspace"), [spaces]);

  /** The scope a report will be created in. "org" is the company connection. */
  const selectedSpace = useMemo(() => {
    if (scope === "org") return orgSpace;
    if (!scope) return null;
    return mySpaces.find((s) => s.id === scope) ?? null;
  }, [scope, orgSpace, mySpaces]);

  function refresh() {
    // Reports are refreshed alongside the schedules: creating or deleting one
    // changes both lists, and a stale report list is the confusing half.
    api.listReports().then(setReports).catch(() => setReports([]));
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
        .then((r) => {
          setSpaces(r.spaces);
          setConnections(r.connections);
        })
        .catch(() => {
          setSpaces([]);
          setConnections([]);
        });
    }
    return () => {
      if (clearTimer.current) clearTimeout(clearTimer.current);
    };
  }, [me]);

  /**
   * Reports grouped by the schedule that produced them, newest group first.
   *
   * A flat list was the wrong shape: the question a reader has is "what does
   * the latest X say", and one row per run buries that under history —
   * twenty schedules times a year of weeks is a thousand rows. Grouping
   * answers it directly and keeps the history one click away.
   *
   * Keyed by scheduler_id, falling back to the title so reports whose
   * schedule was deleted still group together instead of scattering.
   */
  const reportGroups = useMemo(() => {
    const groups = new Map<string, ReportRow[]>();
    for (const report of reports) {
      const key = report.scheduler_id ?? `deleted:${report.title}`;
      const bucket = groups.get(key);
      if (bucket) bucket.push(report);
      else groups.set(key, [report]);
    }
    // `reports` arrives newest-first, so each bucket is already ordered and
    // the map preserves first-seen (= newest) order across groups.
    return [...groups.values()];
  }, [reports]);

  /**
   * Suggestions across EVERY source in the chosen scope.
   *
   * They used to be filtered to the picked service; with no service picked
   * they have to span the scope, which is also the honest shape — the point
   * of a suggestion here is to show what this scope can report on. Round-robin
   * rather than concatenated, for the same reason the chat chips are
   * (`build_combined_suggestions`): with a cap, concatenation means the last
   * source never appears.
   */
  const suggestions = useMemo(() => {
    if (!selectedSpace) return [];
    const perProvider = selectedSpace.providers.map((name) => {
      const connection = connections.find(
        (c) => c.provider === name && c.space_id === (selectedSpace.id ?? null)
      );
      return promptSuggestions(name, connection?.topics ?? []).map((text) => ({ text, provider: name }));
    });
    const interleaved: Array<{ text: string; provider: string }> = [];
    for (let i = 0; interleaved.length < 6; i += 1) {
      const round = perProvider.map((list) => list[i]).filter(Boolean);
      if (!round.length) break;
      interleaved.push(...round);
    }
    return interleaved.slice(0, 6);
  }, [connections, selectedSpace]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (creating || !selectedSpace || !prompt.trim()) return;
    setCreating(true);
    setFormError(null);
    try {
      // No provider: the server reads it off the prompt and tells us which
      // one it landed on, which is what the confirmation below names.
      const created = await api.createScheduler(
        frequency,
        prompt.trim(),
        selectedSpace.id
      );
      // Reset every slot, not just the prompt: the form is the "add another"
      // surface, and a half-filled one reads as "this is still being edited"
      // right after the thing was created.
      setPrompt("");
      setFrequency("weekly");
      setScope("");
      // Naming the detected service is not a nicety: with no dropdown, this
      // confirmation and the chip on the row below are the ONLY places a
      // member can see which source was chosen, and therefore the only way
      // they can catch a misclassification.
      flash(
        `Reading ${PROVIDER_LABEL[created.provider] ?? created.provider}${
          created.workspace_name ? ` in ${created.workspace_name}` : " company-wide"
        } — ${FREQUENCY_LABEL[created.frequency]?.toLowerCase() ?? created.frequency}, first one ${whenLabel(created.next_run_at)}.`
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
      <main className="reports-page">
        <header className="spaces-hero">
          <span className="ask-scope">
            <span className="ask-scope-dot" aria-hidden />
            Reports
          </span>
          <h1>
            Scheduled <span>reports</span>
          </h1>
          <p className="ask-hello-copy">
            Set a task once. It runs daily, weekly or monthly, covers only what changed since the last
            report, and we email you when it is ready to read.
          </p>
          <p className="reports-meta">
            <b>{schedulers.length}</b> schedule{schedulers.length === 1 ? "" : "s"}
            <span aria-hidden>·</span>
            Emailed to <b>{me.email}</b>
          </p>
        </header>

          <section className="room-section reports-new" aria-labelledby="setup-title">
            <div className="studio-section-head">
              <h2 id="setup-title">New report</h2>
              <p className="muted">
                {nothingConnected
                  ? "Nothing schedulable is connected yet."
                  : "Say what you want to know — we work out which app to read. Reports are private to you."}
              </p>
            </div>

            {nothingConnected ? (
              <div className="banner banner-wait" role="status">
                Reports can currently read {SCHEDULABLE_LABELS}. Ask an admin to connect
                one on the Sources page, or connect one inside a space, then come back.
              </div>
            ) : (
              <form onSubmit={create} className="reports-form">
                <div className="reports-composer">
                  <label className="sr-only" htmlFor="prompt">What should the report cover?</label>
                  <textarea
                    id="prompt"
                    rows={2}
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    placeholder="What should the report cover? e.g. what the team discussed, and anything urgent"
                    disabled={creating}
                  />
                  <div className="reports-bar">
                    <ScopePicker
                      value={scope}
                      onChange={setScope}
                      org={orgSpace}
                      spaces={mySpaces}
                      disabled={creating}
                    />
                    <div className="reports-freq" role="radiogroup" aria-label="How often?">
                      {(["daily", "weekly", "monthly"] as const).map((f) => (
                        <button
                          key={f}
                          type="button"
                          role="radio"
                          aria-checked={frequency === f}
                          onClick={() => setFrequency(f)}
                          disabled={creating}
                        >
                          {FREQUENCY_LABEL[f] ?? f}
                        </button>
                      ))}
                    </div>
                    <button
                      className="button reports-create"
                      type="submit"
                      disabled={creating || !selectedSpace || !prompt.trim()}
                    >
                      {creating ? "Creating…" : "Create"}
                    </button>
                  </div>
                </div>
                {/* Two things stated once: which apps get read, and who
                    gets the report. The second is not decoration — the
                    scope names an organisation or a shared space, so
                    without it a reader reasonably assumes their colleagues
                    are about to be emailed a report they did not ask for. */}
                <p className="muted sched-hint">
                  {!scope
                    ? mySpaces.length
                      ? "Choose which apps this report reads: the ones connected for the whole organisation, or one space's own. Either way the report is private to you."
                      : "Reads the apps connected for the whole organisation. The report is private to you."
                    : scope === "org"
                      ? "Reads the apps connected for the whole organisation. Only you receive it — schedules are personal, and nobody else can see this one."
                      : `Reads only ${selectedSpace?.name ?? "this space"}'s own apps, never the company's. Only you receive it — other members of the space are not emailed.`}
                </p>
                {/* Disclosed, not hidden: a space whose only sources have no
                    "what happened since T" feed would otherwise look broken. */}
                {selectedSpace && !selectedSpace.providers.length && (
                  <p className="muted sched-hint">
                    {selectedSpace.connected.length
                      ? `${selectedSpace.name} has ${selectedSpace.connected
                          .map((c) => SOURCE_LABEL[c] ?? c)
                          .join(", ")} connected, which cannot be scheduled yet. Reports need a service with an activity feed: ${SCHEDULABLE_LABELS}.`
                      : `${selectedSpace.name} has nothing connected yet.`}
                  </p>
                )}

                {/* Built from what the chosen connection actually covers; a
                    click writes it into the box, which stays free text. */}
                {suggestions.length > 0 && (
                  <div className="reports-ideas">
                    <p>Ideas from what is connected</p>
                    <ul>
                      {suggestions.slice(0, 4).map((idea, i) => (
                        <li key={idea.text} style={{ ["--i" as string]: i }}>
                          <button type="button" onClick={() => setPrompt(idea.text)} disabled={creating}>
                            <i aria-hidden>
                              {PROVIDER_GLYPH[idea.provider] ? (
                                <BrandGlyph name={PROVIDER_GLYPH[idea.provider]} size={14} />
                              ) : (
                                idea.provider.charAt(0).toUpperCase()
                              )}
                            </i>
                            <span>{idea.text}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {formError && (
                  <div className="banner banner-warn" role="alert">
                    {formError}
                  </div>
                )}
              </form>
            )}
          </section>

          <section className="room-section" aria-labelledby="reports-title">
            <div className="studio-section-head">
              <h2 id="reports-title">Your schedules</h2>
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
                    Describe your first one above and it will show up here.
                  </p>
                </div>
              ) : (
                <ol className="sched-timeline">
                  {[...schedulers]
                    .sort((x, y) => (x.next_run_at ?? "").localeCompare(y.next_run_at ?? ""))
                    .map((scheduler, i) => {
                    const label =
                      PROVIDER_LABEL[scheduler.provider] ?? scheduler.provider;
                    const glyph = PROVIDER_GLYPH[scheduler.provider];
                    const editing = editingId === scheduler.id;
                    const stopped = scheduler.status === "failed";
                    const [day, time] = runDate(scheduler.next_run_at);
                    return (
                      <li
                        key={scheduler.id}
                        className={`sched-item${editing ? " is-editing" : ""}${stopped ? " is-stopped" : ""}`}
                        style={{ ["--i" as string]: i }}
                      >
                        <span className="sched-date" aria-hidden>
                          <b>{day}</b>
                          <small>{time}</small>
                        </span>
                        <span className="sched-dot" aria-hidden>
                          {glyph ? <BrandGlyph name={glyph} size={14} /> : label.charAt(0)}
                        </span>

                        <div className="sched-body">
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
                                  <option value="daily">Daily</option>
                                  <option value="weekly">Weekly</option>
                                  <option value="monthly">Monthly</option>
                                </select>
                              </div>
                            </>
                          ) : (
                            <>
                              {/* The prompt is the instruction, not a name, so
                                  it wraps to two lines rather than being cut
                                  mid-sentence by an ellipsis — the truncated
                                  version made two similar schedules
                                  indistinguishable. */}
                              <strong className="sched-prompt" title={scheduler.prompt}>
                                {scheduler.prompt}
                              </strong>

                              {/* ONE meta line, so the row stays two lines
                                    tall like it was — scope and next run share
                                    it, separated by weight rather than by
                                    another row. Splitting them made the card
                                    50% taller for no extra information.

                                  "Last sent" is deliberately absent: the
                                    Delivered reports list below shows every
                                    send with its timestamp, so repeating the
                                    most recent one here was the same fact
                                    twice on one screen. Only its EXCEPTION is
                                    kept — a scheduler that has never run is
                                    not represented in that list at all. */}
                              <span className="sched-when">
                                <span className="sched-scope">
                                  {label}
                                  <span className="sched-sep" aria-hidden>
                                    ·
                                  </span>
                                  {scheduler.workspace_name || "Company-wide"}
                                </span>
                                <span className="sched-next">
                                  Next {whenLabel(scheduler.next_run_at)}
                                </span>
                                {!scheduler.last_run_at && (
                                  <span className="sched-last">Not sent yet</span>
                                )}
                              </span>

                              {scheduler.last_error && (
                                <span className="sched-error">
                                  {stopped
                                    ? "Stopped after repeated failures: "
                                    : "Last run failed, will retry: "}
                                  {scheduler.last_error}
                                </span>
                              )}
                            </>
                          )}
                        </div>

                        <div className="sched-side">
                          {/* Cadence is the one chip worth always showing: it
                              is how the row is scanned. */}
                          <span className="sched-freq">
                            {FREQUENCY_LABEL[scheduler.frequency] ??
                              scheduler.frequency}
                          </span>
                          {/* Status ONLY when it is not the happy path. Every
                              row reading "ACTIVE" was pure noise — it made the
                              one row that actually needed attention look
                              exactly like the four that did not. */}
                          {(stopped || scheduler.last_error) && (
                            <span
                              className={`studio-chip ${
                                stopped ? "studio-chip-warn" : "studio-chip-wait"
                              }`}
                            >
                              {stopped ? "stopped" : "retrying"}
                            </span>
                          )}
                          <div className="sched-actions">
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
                </ol>
              )}
            </div>
          </section>

        <section className="room-section" aria-labelledby="delivered-title">
          <div className="studio-section-head">
            <h2 id="delivered-title">Delivered reports</h2>
            <p className="muted">
              Every report that has been generated for you. The email is just the
              nudge — the full report lives here.
            </p>
          </div>

          {reportGroups.length === 0 ? (
            <div className="studio-empty">
              <div className="studio-empty-mark" aria-hidden />
              <h3>Nothing delivered yet</h3>
              <p className="muted">
                Your first report arrives one full period after you create a
                schedule, so there is nothing to read on day one.
              </p>
            </div>
          ) : (
            <ul className="report-list">
              {reportGroups.map((group) => {
                const [latest, ...earlier] = group;
                return (
                  <li key={latest.scheduler_id ?? latest.id}>
                    <Link
                      className="report-row"
                      href={`/schedulers/reports/${latest.id}`}
                    >
                      {/* Title = the standing request, which is what tells two
                          schedules on the same service in the same space apart. */}
                      <span className="report-row-title">{latest.title}</span>
                      <span className="report-row-labels">
                        <span className="studio-chip">
                          {FREQUENCY_LABEL[latest.frequency] ?? latest.frequency}
                        </span>
                        <span className="studio-chip">
                          {PROVIDER_LABEL[latest.provider] ?? latest.provider}
                        </span>
                        <span className="studio-chip">
                          {latest.space_name ?? "Company-wide"}
                        </span>
                        {!latest.delivered && (
                          <span className="studio-chip studio-chip-warn">Email failed</span>
                        )}
                      </span>
                      <span className="muted report-row-when">
                        {whenLabel(latest.created_at)}
                      </span>
                    </Link>

                    {/* <details>, not a dropdown with state: the browser owns
                        open/closed, it is keyboard- and screen-reader-correct
                        for free, and nothing is hidden behind a control the
                        reader has to discover. */}
                    {earlier.length > 0 && (
                      <details className="report-history">
                        <summary>
                          {earlier.length} earlier report
                          {earlier.length === 1 ? "" : "s"}
                        </summary>
                        <ul>
                          {earlier.map((report) => (
                            <li key={report.id}>
                              <Link href={`/schedulers/reports/${report.id}`}>
                                <span>{whenLabel(report.created_at)}</span>
                                <span className="muted">
                                  {report.item_count} item
                                  {report.item_count === 1 ? "" : "s"}
                                </span>
                              </Link>
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </main>
    </AppShell>
  );
}
