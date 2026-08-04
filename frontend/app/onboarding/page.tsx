"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { useMe } from "@/lib/useMe";
import { api, ConnectionRecord, JobRecord, MemberRecord, Me } from "@/lib/api";
import { isSetupComplete } from "@/lib/routing";
import { ACTIVE_JOB_STATUSES, useJobPolling } from "@/lib/jobPoll";

const ACTIVE = ACTIVE_JOB_STATUSES;

const PROVIDER_LABELS: Record<string, string> = {
  notion: "Notion",
  google: "Google Drive",
};

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

function syncCompleteMessage(docCount: number | null | undefined): string {
  if (docCount != null) {
    return `You’re ready — ${docCount} policy document${docCount === 1 ? "" : "s"} loaded. Start asking anytime.`;
  }
  return "You’re ready — your policies are loaded. Start asking anytime.";
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
  const [folderUrl, setFolderUrl] = useState("");
  const [savingFolder, setSavingFolder] = useState(false);
  const prevJobStatus = useRef<string | null>(null);
  const announcedSuccess = useRef(false);
  const bootstrapped = useRef(false);

  const effectiveMe = localMe ?? me;

  // Prefer the connection that just completed OAuth; otherwise first connected source.
  const primary = useMemo(() => {
    const connectedProvider = searchParams.get("connected");
    if (connectedProvider) {
      const match = connections.find((c) => c.provider === connectedProvider);
      if (match) return match;
    }
    return (
      connections.find((c) => c.provider === "notion" || c.provider === "google") ??
      connections[0]
    );
  }, [connections, searchParams]);

  const providerLabel = primary
    ? PROVIDER_LABELS[primary.provider] || primary.provider
    : "source";
  const needsFolder =
    primary?.provider === "google" && !primary.source_config?.folder_id;
  const canSync = Boolean(primary) && !needsFolder;

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

  const syncInProgress = busy || activeJob != null || Boolean(effectiveMe?.sync_in_progress);

  useEffect(() => {
    if (me) setLocalMe(me);
  }, [me]);

  // Reconnecting Drive from Sources still lands on /onboarding?connected=…
  // Send already-setup admins back to Sources instead of the first-run flow.
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
        ? `${label} connected. Next: choose a Drive folder, then sync.`
        : `${label} connected. Next: sync your policies.`
    );
  }, [searchParams, effectiveMe]);

  useEffect(() => {
    if (!me || me.role !== "admin" || bootstrapped.current) return;
    bootstrapped.current = true;
    api.listConnections().then(setConnections).catch(() => undefined);
    api.listMembers().then(setMembers).catch(() => undefined);
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

  // Poll only while a job is active (or we just kicked one off) — not forever until ready.
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

  async function handleSaveFolder(e: React.FormEvent) {
    e.preventDefault();
    if (!primary || primary.provider !== "google") return;
    setSavingFolder(true);
    setError(null);
    try {
      const result = await api.setConnectionConfig(primary.id, folderUrl.trim());
      setConnections((prev) =>
        prev.map((c) =>
          c.id === primary.id ? { ...c, source_config: result.config } : c
        )
      );
      setFolderUrl("");
      setMessage("Folder saved. You can sync your policies now.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save folder.");
    } finally {
      setSavingFolder(false);
    }
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

  // Step 2 until a full succeeded sync — never advance mid-ingest.
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
        <div>
          <p className="eyebrow">
            Welcome{effectiveMe.org_name ? ` to ${effectiveMe.org_name}` : ""}
          </p>
          <h1>Welcome — let’s get you set up</h1>
          <p className="muted">
            Connect where your policies live, bring them in, then invite your team.
          </p>
        </div>

        {message && !syncInProgress && <div className="banner banner-ok">{message}</div>}
        {error && <div className="banner banner-warn">{error}</div>}

        {step === 1 && (
          <section className="card stack">
            <h2>1. Connect your policies</h2>
            <p className="muted">
              Choose Notion or Google Drive — whichever already holds your company policies.
            </p>
            <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
              <a className="button" href={api.connectUrl("notion")}>
                Connect Notion
              </a>
              <a className="button button-secondary" href={api.connectUrl("google")}>
                Connect Google Drive
              </a>
            </div>
          </section>
        )}

        {step === 2 && (
          <section className="card stack">
            <h2>2. Bring your policies in</h2>
            <p className="muted">
              {primary?.external_workspace_name
                ? `Connected to “${primary.external_workspace_name}” (${providerLabel}). `
                : `${providerLabel} is connected. `}
              {needsFolder
                ? "Choose a Drive folder before continuing."
                : "Bring in the shared policy pages, then wait here until it finishes."}
            </p>

            {needsFolder && (
              <form onSubmit={handleSaveFolder} className="stack">
                <div className="field">
                  <label htmlFor="onboarding-folder">Drive folder URL</label>
                  <input
                    id="onboarding-folder"
                    className="input"
                    type="text"
                    required
                    placeholder="https://drive.google.com/drive/folders/…"
                    value={folderUrl}
                    onChange={(e) => setFolderUrl(e.target.value)}
                  />
                </div>
                <button
                  className="button"
                  type="submit"
                  disabled={savingFolder || !folderUrl.trim()}
                >
                  {savingFolder ? "Saving…" : "Save folder"}
                </button>
              </form>
            )}

            {syncInProgress && (
              <div className="banner banner-wait" role="status" aria-live="polite">
                <div className="sync-wait-row">
                  <span className="sync-spinner" aria-hidden />
                  <div>
                    <strong>Bringing your policies in…</strong>
                    <p className="muted" style={{ margin: "0.35rem 0 0" }}>
                      This can take a few minutes. Keep this page open until it finishes.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {displayJob?.status === "failed" && (
              <p className="muted">{displayJob.error || "Sync failed. Please try again."}</p>
            )}

            {!needsFolder && (
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
                      ? "Try again"
                      : "Bring them in"}
              </button>
            )}
          </section>
        )}

        {step === 3 && (
          <section className="card stack">
            <div className="banner banner-ok">
              <strong>You’re ready</strong>
              <p style={{ margin: "0.4rem 0 0" }}>
                {docCount != null
                  ? `${docCount} policy document${docCount === 1 ? "" : "s"} loaded. Start asking anytime.`
                  : "Your policies are loaded. Start asking anytime."}
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
