"use client";

import { Fragment, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { BrandGlyph } from "@/components/BrandGlyph";
import { DriveFolderPicker } from "@/components/DriveFolderPicker";
import { LinearTeamPicker } from "@/components/LinearTeamPicker";
import { OwnAppSetup } from "@/components/OwnAppSetup";
import { SlackChannelPicker } from "@/components/SlackChannelPicker";
import { useMe } from "@/lib/useMe";
import { api, ConnectionRecord, ConnectorApp, JobRecord, MemberRecord, Me } from "@/lib/api";
import { isSetupComplete } from "@/lib/routing";
import { ACTIVE_JOB_STATUSES, useJobPolling } from "@/lib/jobPoll";

const ACTIVE = ACTIVE_JOB_STATUSES;

const PROVIDER_LABELS: Record<string, string> = {
  notion: "Notion",
  google: "Google Drive",
  slack: "Slack",
  linear: "Linear",
};

// The sources onboarding brings documents in from. GitHub is read live, with
// nothing to bring in, so it is added from Sources once setup is done.
const SOURCES: { provider: "notion" | "google" | "slack" | "linear"; blurb: string }[] = [
  { provider: "notion", blurb: "Pages and databases your workspace shares with Handbook." },
  { provider: "google", blurb: "Docs, Sheets and files in a folder you choose." },
  { provider: "slack", blurb: "Conversations in the channels you pick." },
  { provider: "linear", blurb: "Issues and projects of the teams you pick." },
];

function pickDisplayJob(
  jobs: JobRecord[],
  connectionId: string,
  preferSucceeded: boolean
): JobRecord | undefined {
  const mine = jobs
    .filter((j) => j.connection_id === connectionId)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  if (preferSucceeded) {
    return mine.find((j) => j.status === "succeeded") ?? mine[0];
  }
  return mine.find((j) => ACTIVE.has(j.status)) ?? mine[0];
}

function syncHeadline(job: JobRecord | undefined): string {
  const phase = job?.phase;
  if (phase === "listing") return "Looking at what's there…";
  if (phase === "preparing") return "Opening the next page…";
  if (phase === "contextualizing") return "Adding search context…";
  if (phase === "embedding") return "Indexing for search…";
  return "Bringing your documents in…";
}

function syncDetail(job: JobRecord | undefined): string {
  const total = job?.total_documents ?? null;
  const done = job?.processed_documents ?? 0;
  if (job?.phase === "listing") {
    return "Checking your source for pages that are new or changed.";
  }
  if (total != null && total > 0) {
    const current = Math.min(total, done + 1);
    if (done < total) {
      return `Page ${current} of ${total} — ${done} stored so far. You can invite people below while this finishes.`;
    }
    return `${done} of ${total} pages done.`;
  }
  if (total === 0) return "No new pages found — finishing up.";
  return "This usually takes under a minute. Search quality may keep improving briefly in the background.";
}

function syncPercent(job: JobRecord | undefined): number | null {
  const total = job?.total_documents ?? null;
  if (total == null || total <= 0) return null;
  const done = job?.processed_documents ?? 0;
  return Math.min(100, Math.round((done / total) * 100));
}

function syncCompleteMessage(docCount: number | null | undefined): string {
  if (docCount != null) {
    return `You’re ready — ${docCount} document${docCount === 1 ? "" : "s"} loaded. Start asking anytime.`;
  }
  return "You’re ready — your documents are loaded. Start asking anytime.";
}

function OnboardingInner() {
  const { me, loading, refresh } = useMe({ enforceSetupFlow: true });
  const router = useRouter();
  const searchParams = useSearchParams();
  const [connections, setConnections] = useState<ConnectionRecord[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [members, setMembers] = useState<MemberRecord[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pollToken, setPollToken] = useState(0);
  const [watchedJobId, setWatchedJobId] = useState<string | null>(null);
  const [localMe, setLocalMe] = useState<Me | null>(null);
  const [apps, setApps] = useState<{ google: ConnectorApp; slack: ConnectorApp } | null>(null);
  // Apps saved on this page: their setup stays open (Slack has a step after
  // the keys), with the Connect row under it.
  const [appsSaved, setAppsSaved] = useState<string[]>([]);
  const prevJobStatus = useRef<string | null>(null);
  const announcedSuccess = useRef(false);
  const bootstrapped = useRef(false);

  const effectiveMe = localMe ?? me;

  const primary = useMemo(() => {
    const connectedProvider = searchParams.get("connected");
    if (connectedProvider) {
      const match = connections.find((c) => c.provider === connectedProvider);
      if (match) return match;
    }
    return connections.find((c) => c.provider in PROVIDER_LABELS) ?? connections[0];
  }, [connections, searchParams]);

  const providerLabel = primary
    ? PROVIDER_LABELS[primary.provider] || primary.provider
    : "source";
  const needsFolder =
    primary?.provider === "google" && !primary.source_config?.folder_id;
  // Slack and Linear read only what the admin picks (autosync.SCOPE_KEYS).
  const needsChannels = primary?.provider === "slack" && !primary.source_config?.channel_ids?.length;
  const needsTeams = primary?.provider === "linear" && !primary.source_config?.team_ids?.length;
  const needsScope = needsFolder || needsChannels || needsTeams;
  const canSync = Boolean(primary) && !needsScope;

  const displayJob = useMemo(() => {
    if (!primary) return undefined;
    return pickDisplayJob(jobs, primary.id, Boolean(effectiveMe?.ready_to_ask));
  }, [jobs, primary, effectiveMe?.ready_to_ask]);

  const activeJob = useMemo(() => {
    if (!primary) return undefined;
    if (effectiveMe?.ready_to_ask) return undefined;
    return jobs
      .filter((j) => j.connection_id === primary.id && ACTIVE.has(j.status))
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  }, [jobs, primary, effectiveMe?.ready_to_ask]);

  const syncInProgress =
    busy ||
    activeJob != null ||
    Boolean(effectiveMe?.sync_in_progress && !effectiveMe?.ready_to_ask);

  useEffect(() => {
    if (me) setLocalMe(me);
  }, [me]);

  useEffect(() => {
    if (!effectiveMe) return;
    if (searchParams.get("connected") && isSetupComplete(effectiveMe)) {
      router.replace("/admin/connections");
    }
  }, [effectiveMe, searchParams, router]);

  useEffect(() => {
    const connected = searchParams.get("connected");
    if (!connected) return;
    if (effectiveMe && isSetupComplete(effectiveMe)) return;
    const label = PROVIDER_LABELS[connected] || connected;
    setMessage(
      connected === "google"
        ? `${label} connected. Next: choose a Drive folder.`
        : connected === "slack"
          ? `${label} connected. Next: pick the channels to read.`
          : connected === "linear"
            ? `${label} connected. Next: pick the teams to read.`
            : `${label} connected. Next: sync your documents.`
    );
  }, [searchParams, effectiveMe]);

  useEffect(() => {
    if (!me || me.role !== "admin" || bootstrapped.current) return;
    bootstrapped.current = true;
    api.listConnections().then(setConnections).catch(() => undefined);
    api.listMembers().then(setMembers).catch(() => undefined);
    api.listConnectorApps().then(setApps).catch(() => undefined);
    api.listJobs().then((list) => {
      setJobs(list);
      const active = list.find((j) => ACTIVE.has(j.status));
      if (active) {
        setWatchedJobId(active.id);
        setPollToken((n) => n + 1);
      }
    }).catch(() => undefined);
  }, [me]);

  const applyMeSnapshot = useCallback((fresh: Me) => {
    setLocalMe((prev) => {
      if (
        prev &&
        prev.has_documents === fresh.has_documents &&
        prev.has_connection === fresh.has_connection &&
        prev.ready_to_ask === fresh.ready_to_ask &&
        prev.sync_in_progress === fresh.sync_in_progress &&
        prev.latest_job_status === fresh.latest_job_status &&
        prev.latest_doc_count === fresh.latest_doc_count
      ) {
        return prev;
      }
      return fresh;
    });
  }, []);

  const announceReady = useCallback(
    (docCount: number | null | undefined) => {
      if (announcedSuccess.current) return;
      announcedSuccess.current = true;
      setMessage(syncCompleteMessage(docCount));
      setError(null);
      refresh().catch(() => undefined);
    },
    [refresh]
  );

  const pollEnabled =
    Boolean(me && me.role === "admin") &&
    (watchedJobId != null || Boolean(activeJob) || busy);

  useJobPolling({
    enabled: pollEnabled,
    jobId: watchedJobId,
    pollToken,
    onJobs: (fetched) => {
      setJobs((prev) => {
        if (!watchedJobId) return fetched;
        const byId = new Map(prev.map((j) => [j.id, j]));
        for (const j of fetched) byId.set(j.id, j);
        return Array.from(byId.values()).sort((a, b) =>
          b.created_at.localeCompare(a.created_at)
        );
      });
      return fetched.some((j) => ACTIVE.has(j.status));
    },
  });

  useEffect(() => {
    if (!displayJob) return;
    const prev = prevJobStatus.current;
    const curr = displayJob.status;
    if (prev && ACTIVE.has(prev) && curr === "succeeded") {
      setWatchedJobId(null);
      api.me().then((fresh) => {
        applyMeSnapshot(fresh);
        if (fresh.ready_to_ask) {
          announceReady(displayJob.doc_count ?? fresh.latest_doc_count);
        }
      }).catch(() => undefined);
    } else if (prev && ACTIVE.has(prev) && curr === "failed") {
      setWatchedJobId(null);
      setError(displayJob.error || "Sync failed. Please try again.");
      setMessage(null);
    }
    prevJobStatus.current = curr;
  }, [displayJob, applyMeSnapshot, announceReady]);

  function scopeSaved(config: ConnectionRecord["source_config"], what: string) {
    if (!primary) return;
    setConnections((prev) => prev.map((c) => (c.id === primary.id ? { ...c, source_config: config } : c)));
    setError(null);
    setMessage(`${what} saved. Bringing your documents in now.`);
    // Saving the scope queues the first sync server-side; follow that job.
    api.listJobs().then((list) => {
      setJobs(list);
      const queued = list.find((j) => j.connection_id === primary.id && ACTIVE.has(j.status));
      if (queued) {
        setWatchedJobId(queued.id);
        setPollToken((n) => n + 1);
      }
    }).catch(() => undefined);
  }

  async function handleIngest() {
    if (!primary || !canSync || syncInProgress) return;
    setBusy(true);
    setError(null);
    announcedSuccess.current = false;
    setMessage(null);
    try {
      const { job_id } = await api.triggerIngest(primary.id);
      setWatchedJobId(job_id);
      const job = await api.getJob(job_id).catch(() => null);
      if (job) {
        setJobs((prev) => [job, ...prev.filter((j) => j.id !== job.id)]);
      }
      setPollToken((n) => n + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start sync.");
      setMessage(null);
      setWatchedJobId(null);
    } finally {
      setBusy(false);
    }
  }

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const invited = await api.inviteMember(inviteEmail.trim().toLowerCase());
      const who = inviteEmail.trim().toLowerCase();
      setInviteEmail("");
      setMessage(
        invited.dev_link
          ? `Added ${who}. Dev link (console email): ${invited.dev_link}`
          : `Invite sent to ${who}.`
      );
      api.listMembers().then(setMembers);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not invite that email.");
    }
  }

  if (loading || !effectiveMe) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  const step: 1 | 2 | 3 = !effectiveMe.has_connection
    ? 1
    : !effectiveMe.ready_to_ask
      ? 2
      : 3;

  const docCount =
    displayJob?.status === "succeeded"
      ? displayJob.doc_count
      : effectiveMe.latest_doc_count;

  return (
    <AppShell me={effectiveMe} variant="onboarding">
      <main className="page stack onboarding-main">
        <div className="onboarding-hero">
          {effectiveMe.org_name && <p className="eyebrow">{effectiveMe.org_name}</p>}
          <h1>Set up Handbook</h1>
          <p className="muted">
            Connect where your documents live, bring them in, then invite your team.
          </p>
          <p className="onboarding-privacy">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
              <rect x="5" y="11" width="14" height="9" rx="2" stroke="currentColor" strokeWidth="1.8" />
              <path d="M8 11V8a4 4 0 0 1 8 0v3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
            Private to your company. Never used to train AI models or shared with anyone else.
          </p>
        </div>

        {message && !syncInProgress && <div className="banner banner-ok">{message}</div>}
        {error && <div className="banner banner-warn">{error}</div>}

        {step === 1 && (
          <section className="card stack">
            <div className="onboarding-step-head">
              <span className="onboarding-step-label">Step 1 of 3</span>
              <h2>Connect your first source</h2>
              <p className="muted">
                Start with the one that holds most of your company&apos;s knowledge. You can add the
                others, and GitHub, from Sources later.
              </p>
            </div>
            <div className="source-bento">
              {SOURCES.map(({ provider, blurb }) => {
                const ownApp = provider === "google" || provider === "slack" ? apps?.[provider] : undefined;
                // Google Drive and Slack connect through the company's own app.
                const appMissing = Boolean(ownApp && !ownApp.configured && !ownApp.server_app);
                return (
                  <Fragment key={provider}>
                    {ownApp && (appMissing || appsSaved.includes(provider)) && (
                      <OwnAppSetup
                        provider={provider as "google" | "slack"}
                        app={ownApp}
                        asSource
                        onSaved={(updated) => {
                          setApps((prev) => (prev ? { ...prev, [provider]: updated } : prev));
                          setAppsSaved((prev) => [...prev, provider]);
                        }}
                      />
                    )}
                    {!appMissing && (
                      <div className="card source-row">
                        <div className="source-row-head">
                          <div className="source-row-main">
                            <span className="source-row-mark">
                              <BrandGlyph name={provider} size={28} />
                            </span>
                            <div className="source-row-copy">
                              <h3>{PROVIDER_LABELS[provider]}</h3>
                              <p className="source-row-kind">{blurb}</p>
                            </div>
                          </div>
                          <div className="source-row-actions">
                            <a className="button" href={api.connectUrl(provider)}>
                              Connect
                            </a>
                          </div>
                        </div>
                      </div>
                    )}
                  </Fragment>
                );
              })}
            </div>
          </section>
        )}

        {step === 2 && (
          <section className="card stack">
            <div className="onboarding-step-head">
              <span className="onboarding-step-label">Step 2 of 3</span>
              <h2>Bring your documents in</h2>
            </div>
            <p className="muted">
              {primary?.external_workspace_name
                ? `Connected to “${primary.external_workspace_name}” (${providerLabel}). `
                : `${providerLabel} is connected. `}
              {needsFolder
                ? "Choose a Drive folder before continuing."
                : needsChannels
                  ? "Pick the channels Handbook should read."
                  : needsTeams
                    ? "Pick the teams whose issues Handbook should read."
                    : "Bring in the shared pages, then wait here until it finishes."}
            </p>

            {needsFolder && primary && (
              <DriveFolderPicker
                connectionId={primary.id}
                inputId="onboarding-folder"
                onSaved={(config) => {
                  setConnections((prev) =>
                    prev.map((c) =>
                      c.id === primary.id ? { ...c, source_config: config } : c
                    )
                  );
                  setError(null);
                  setMessage("Folder saved. You can sync your documents now.");
                }}
                onError={(message) => {
                  if (message) setError(message);
                }}
              />
            )}

            {needsChannels && primary && (
              <SlackChannelPicker
                connectionId={primary.id}
                onSaved={(config) => scopeSaved(config, "Channels")}
                onError={(message) => setError(message || null)}
              />
            )}

            {needsTeams && primary && (
              <LinearTeamPicker
                connectionId={primary.id}
                onSaved={(config) => scopeSaved(config, "Teams")}
                onError={(message) => setError(message || null)}
              />
            )}

            {syncInProgress && (
              <div className="banner banner-wait" role="status" aria-live="polite">
                <div className="sync-wait-row">
                  <span className="sync-spinner" aria-hidden />
                  <div style={{ flex: 1 }}>
                    <strong>{syncHeadline(activeJob)}</strong>
                    <p className="muted" style={{ margin: "0.35rem 0 0" }}>
                      {syncDetail(activeJob)}
                    </p>
                    {syncPercent(activeJob) != null && (
                      <div
                        className="sync-progress"
                        role="progressbar"
                        aria-valuenow={syncPercent(activeJob) ?? 0}
                        aria-valuemin={0}
                        aria-valuemax={100}
                      >
                        <div
                          className="sync-progress-bar"
                          style={{ width: `${syncPercent(activeJob)}%` }}
                        />
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {displayJob?.status === "failed" && (
              <p className="muted">{displayJob.error || "Sync failed. Please try again."}</p>
            )}

            {!needsScope && (
              <button
                className="button"
                type="button"
                onClick={handleIngest}
                disabled={syncInProgress || !canSync}
              >
                {syncInProgress
                  ? "Syncing…"
                  : displayJob?.status === "failed"
                    ? "Try again"
                    : displayJob
                      ? "Sync again"
                      : "Bring them in"}
              </button>
            )}

            {(syncInProgress || displayJob?.status === "failed") && (
              <div className="stack" style={{ marginTop: "1.25rem" }}>
                <h3 style={{ margin: 0, fontSize: "1rem" }}>Invite while you wait</h3>
                <p className="muted" style={{ margin: 0 }}>
                  Sync runs in the background. Add a teammate now — they can sign in once documents are ready.
                </p>
                <form
                  onSubmit={handleInvite}
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    alignItems: "center",
                    gap: "0.85rem",
                  }}
                >
                  <input
                    className="input"
                    type="email"
                    required
                    placeholder="teammate@company.com"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                    autoComplete="email"
                    style={{ flex: "1 1 12rem", minWidth: "12rem" }}
                  />
                  <button className="button button-secondary" type="submit" disabled={!inviteEmail.trim()}>
                    Send invite
                  </button>
                </form>
              </div>
            )}
          </section>
        )}

        {step === 3 && (
          <section className="card stack">
            <div className="banner banner-ok">
              <strong>You’re ready</strong>
              <p style={{ margin: "0.4rem 0 0" }}>
                {docCount != null
                  ? `${docCount} document${docCount === 1 ? "" : "s"} loaded. Start asking anytime.`
                  : "Your documents are loaded. Start asking anytime."}
              </p>
            </div>

            <button
              className="button"
              type="button"
              onClick={() => router.push("/chat")}
              disabled={!isSetupComplete(effectiveMe)}
            >
              Start asking →
            </button>

            <div className="invite-section">
              <h2>Invite your teammates</h2>
              <p className="muted">Optional — we’ll email them a sign-in link.</p>
              <form onSubmit={handleInvite} className="invite-form">
                <div className="field">
                  <label htmlFor="invite">Work email</label>
                  <input
                    id="invite"
                    className="input"
                    type="email"
                    required
                    placeholder="teammate@company.com"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                  />
                </div>
                <button className="button button-secondary" type="submit">
                  Send invite
                </button>
              </form>
              {members.length > 0 && (
                <ul className="member-list">
                  {members.map((m) => (
                    <li key={m.id}>
                      <span>{m.email}</span>
                      <span className="badge">{m.role}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        )}
      </main>
    </AppShell>
  );
}

export default function OnboardingPage() {
  return (
    <Suspense
      fallback={
        <main className="page">
          <p className="muted">Loading…</p>
        </main>
      }
    >
      <OnboardingInner />
    </Suspense>
  );
}
