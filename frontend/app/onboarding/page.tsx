"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { useMe } from "@/lib/useMe";
import { api, ConnectionRecord, JobRecord, MemberRecord, Me } from "@/lib/api";
import { isSetupComplete } from "@/lib/routing";

const ACTIVE = new Set(["queued", "running"]);
const POLL_MS = 2500;

function pickDisplayJob(
  jobs: JobRecord[],
  connectionId: string,
  preferFinishedWhenDocsReady: boolean
): JobRecord | undefined {
  const mine = jobs
    .filter((j) => j.connection_id === connectionId)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  // Once docs are in the DB, prefer a succeeded job over a leftover queued row
  // so the UI doesn't look stuck while the wizard already advanced.
  if (preferFinishedWhenDocsReady) {
    return mine.find((j) => j.status === "succeeded") ?? mine[0];
  }
  return mine.find((j) => ACTIVE.has(j.status)) ?? mine[0];
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
  /** Bumped on Sync click so the poller restarts if it had already stopped. */
  const [pollToken, setPollToken] = useState(0);
  /** Local mirror so we can advance the wizard without thrashing useMe. */
  const [localMe, setLocalMe] = useState<Me | null>(null);
  const prevJobStatus = useRef<string | null>(null);
  const announcedSuccess = useRef(false);
  const bootstrapped = useRef(false);

  const effectiveMe = localMe ?? me;

  const notion = useMemo(
    () => connections.find((c) => c.provider === "notion"),
    [connections]
  );
  const displayJob = useMemo(() => {
    if (!notion) return undefined;
    return pickDisplayJob(jobs, notion.id, Boolean(effectiveMe?.ready_to_ask));
  }, [jobs, notion, effectiveMe?.ready_to_ask]);

  const activeJob = useMemo(() => {
    if (!notion) return undefined;
    // Ignore leftover queued/running rows once sync is fully ready.
    if (effectiveMe?.ready_to_ask) return undefined;
    return jobs
      .filter((j) => j.connection_id === notion.id && ACTIVE.has(j.status))
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  }, [jobs, notion, effectiveMe?.ready_to_ask]);

  const syncInProgress = busy || activeJob != null;

  // Sync localMe when session first loads / refresh returns new flags.
  useEffect(() => {
    if (me) setLocalMe(me);
  }, [me]);

  useEffect(() => {
    if (searchParams.get("connected")) {
      setMessage("Notion connected — next, sync your policy pages.");
    }
  }, [searchParams]);

  // One-shot bootstrap of connections + members (do NOT re-run on every me refresh).
  useEffect(() => {
    if (!me || me.role !== "admin" || bootstrapped.current) return;
    bootstrapped.current = true;
    api.listConnections().then(setConnections).catch(() => undefined);
    api.listMembers().then(setMembers).catch(() => undefined);
    api.listJobs().then(setJobs).catch(() => undefined);
  }, [me]);

  const applyMeSnapshot = useCallback((fresh: Me) => {
    setLocalMe((prev) => {
      if (
        prev &&
        prev.has_documents === fresh.has_documents &&
        prev.has_connection === fresh.has_connection &&
        prev.ready_to_ask === fresh.ready_to_ask &&
        prev.sync_in_progress === fresh.sync_in_progress
      ) {
        return prev;
      }
      return fresh;
    });
  }, []);

  // Stable job poller — runs until ready_to_ask (full ingest finished).
  // Depends on pollToken so a new Sync click restarts polling if needed.
  useEffect(() => {
    if (!me || me.role !== "admin") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      try {
        const list = await api.listJobs();
        if (cancelled) return;
        setJobs(list);

        const fresh = await api.me().catch(() => null);
        if (cancelled) return;
        if (fresh) {
          applyMeSnapshot(fresh);
          // Only celebrate when the job finished — not when the first page lands.
          if (fresh.ready_to_ask && !announcedSuccess.current) {
            announcedSuccess.current = true;
            const succeeded = list.find((j) => j.status === "succeeded");
            const n = succeeded?.doc_count;
            setMessage(
              n != null
                ? `Sync complete — ${n} document${n === 1 ? "" : "s"} ready. You can ask questions now.`
                : "Sync complete — your policies are ready. You can ask questions now."
            );
            setError(null);
            refresh().catch(() => undefined);
          }
        }

        // Stay on this step until ingest finishes (ready_to_ask).
        const done = Boolean(fresh?.ready_to_ask);
        if (!cancelled && !done) {
          timer = setTimeout(tick, POLL_MS);
        }
      } catch {
        // API restart mid-poll → "Failed to fetch"; keep trying quietly.
        if (!cancelled) {
          timer = setTimeout(tick, POLL_MS * 2);
        }
      }
    }

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me?.user_id, pollToken]);

  // Banner when an active job transitions to succeeded/failed.
  useEffect(() => {
    if (!displayJob) return;
    const prev = prevJobStatus.current;
    const curr = displayJob.status;
    if (prev && ACTIVE.has(prev) && curr === "succeeded") {
      announcedSuccess.current = true;
      const n = displayJob.doc_count;
      setMessage(
        n != null
          ? `Sync complete — ${n} document${n === 1 ? "" : "s"} ready.`
          : "Sync complete — your policies are ready."
      );
      setError(null);
      api.me().then(applyMeSnapshot).catch(() => undefined);
      refresh().catch(() => undefined);
    } else if (prev && ACTIVE.has(prev) && curr === "failed") {
      setError(displayJob.error || "Sync failed. Try again.");
      setMessage(null);
    }
    prevJobStatus.current = curr;
  }, [displayJob, applyMeSnapshot, refresh]);

  async function handleIngest() {
    if (!notion || syncInProgress) return;
    setBusy(true);
    setError(null);
    announcedSuccess.current = false;
    setPollToken((n) => n + 1);
    setMessage("Sync started — this can take a minute. Keep this page open.");
    try {
      await api.triggerIngest(notion.id);
      const list = await api.listJobs();
      setJobs(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start sync.");
      setMessage(null);
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
          : `Added ${who} and emailed a sign-in link — they can open it to join Ask.`
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

  // Stay on Sync until the ingest job finishes — partial docs must not unlock Ask.
  const step: 1 | 2 | 3 = !effectiveMe.has_connection
    ? 1
    : !effectiveMe.ready_to_ask
      ? 2
      : 3;

  return (
    <AppShell me={effectiveMe} variant="onboarding">
      <main className="page stack onboarding-main">
        <div>
          <p className="eyebrow">
            Welcome{effectiveMe.org_name ? ` to ${effectiveMe.org_name}` : ""}
          </p>
          <h1>Set up your policy portal</h1>
          <p className="muted">
            Three short steps. Your team can ask questions only after policies are synced.
          </p>
        </div>

        {message && <div className="banner banner-ok">{message}</div>}
        {error && <div className="banner banner-warn">{error}</div>}

        {step === 1 && (
          <section className="card stack">
            <h2>1. Connect Notion</h2>
            <p className="muted">
              Link the workspace that holds your HR / policy docs. You choose which pages to
              share with the integration on Notion&rsquo;s consent screen.
            </p>
            <a className="button" href={api.connectUrl("notion")}>
              Connect Notion
            </a>
          </section>
        )}

        {step === 2 && (
          <section className="card stack">
            <h2>2. Sync policy documents</h2>
            <p className="muted">
              {notion?.external_workspace_name
                ? `Connected to “${notion.external_workspace_name}”. `
                : "Notion is connected. "}
              Run a sync to pull shared pages into your organization.
            </p>
            {displayJob && (
              <p className="muted" style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <JobStatusBadge status={displayJob.status} />
                {displayJob.status === "succeeded" && displayJob.doc_count != null
                  ? `${displayJob.doc_count} documents synced`
                  : displayJob.status === "failed"
                    ? displayJob.error || "Sync failed"
                    : displayJob.status === "queued"
                      ? "Queued — waiting for the ingestion worker…"
                      : "Sync in progress…"}
              </p>
            )}
            <button
              className="button"
              type="button"
              onClick={handleIngest}
              disabled={syncInProgress || !notion}
            >
              {syncInProgress
                ? activeJob?.status === "queued"
                  ? "Waiting for worker…"
                  : "Syncing…"
                : displayJob
                  ? "Sync again"
                  : "Start sync"}
            </button>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              Sync runs inside the API server — keep{" "}
              <code className="mono">uvicorn</code> running until it finishes. Stay on this page;
              Ask unlocks only after every shared page is ingested.
            </p>
          </section>
        )}

        {step === 3 && (
          <section className="card stack">
            <h2>3. Invite your team</h2>
            <p className="muted">
              Policies are ready. Invite adds them to your org and emails a sign-in link — they
              only see Ask for your organization.
            </p>
            {displayJob?.status === "succeeded" && displayJob.doc_count != null && (
              <div className="banner banner-ok">
                Last sync brought in {displayJob.doc_count} document
                {displayJob.doc_count === 1 ? "" : "s"}.
              </div>
            )}
            <form onSubmit={handleInvite} className="stack" style={{ maxWidth: "420px" }}>
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
              <button className="button" type="submit">
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
            <button
              className="button button-secondary"
              type="button"
              onClick={() => router.push("/chat")}
              disabled={!isSetupComplete(effectiveMe)}
            >
              Go to Ask →
            </button>
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
