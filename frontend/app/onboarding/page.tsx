"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { useMe } from "@/lib/useMe";
import { api, ConnectionRecord, JobRecord, MemberRecord, Me } from "@/lib/api";
import { isSetupComplete } from "@/lib/routing";

const ACTIVE = new Set(["queued", "running"]);
const POLL_MS = 2500;

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
    return `Sync complete — ${docCount} policy document${docCount === 1 ? "" : "s"} ready. You can ask questions now.`;
  }
  return "Sync complete — your policies are ready. You can ask questions now.";
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
    if (effectiveMe?.ready_to_ask) return undefined;
    return jobs
      .filter((j) => j.connection_id === notion.id && ACTIVE.has(j.status))
      .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  }, [jobs, notion, effectiveMe?.ready_to_ask]);

  const syncInProgress = busy || activeJob != null || Boolean(effectiveMe?.sync_in_progress);

  useEffect(() => {
    if (me) setLocalMe(me);
  }, [me]);

  useEffect(() => {
    if (searchParams.get("connected")) {
      setMessage("Notion connected. Next: sync your policies.");
    }
  }, [searchParams]);

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

  // Poll until a full sync has succeeded (ready_to_ask). Never unlock mid-ingest.
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
          if (fresh.ready_to_ask) {
            const succeeded = list.find((j) => j.status === "succeeded");
            announceReady(succeeded?.doc_count ?? fresh.latest_doc_count);
          }
        }

        if (!cancelled && !fresh?.ready_to_ask) {
          timer = setTimeout(tick, POLL_MS);
        }
      } catch {
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

  useEffect(() => {
    if (!displayJob) return;
    const prev = prevJobStatus.current;
    const curr = displayJob.status;
    if (prev && ACTIVE.has(prev) && curr === "succeeded") {
      // Wait for /me.ready_to_ask on the next poll tick — don't unlock on job
      // status alone if documents haven't committed yet.
      api.me().then((fresh) => {
        applyMeSnapshot(fresh);
        if (fresh.ready_to_ask) {
          announceReady(displayJob.doc_count ?? fresh.latest_doc_count);
        }
      }).catch(() => undefined);
    } else if (prev && ACTIVE.has(prev) && curr === "failed") {
      setError(displayJob.error || "Sync failed. Please try again.");
      setMessage(null);
    }
    prevJobStatus.current = curr;
  }, [displayJob, applyMeSnapshot, announceReady]);

  async function handleIngest() {
    if (!notion || syncInProgress) return;
    setBusy(true);
    setError(null);
    announcedSuccess.current = false;
    setPollToken((n) => n + 1);
    setMessage(null);
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
          <h1>Set up your policy portal</h1>
          <p className="muted">Connect Notion, sync your policies, then invite your team.</p>
        </div>

        {message && !syncInProgress && <div className="banner banner-ok">{message}</div>}
        {error && <div className="banner banner-warn">{error}</div>}

        {step === 1 && (
          <section className="card stack">
            <h2>1. Connect Notion</h2>
            <p className="muted">
              Connect the workspace that holds your company policies.
            </p>
            <a className="button" href={api.connectUrl("notion")}>
              Connect Notion
            </a>
          </section>
        )}

        {step === 2 && (
          <section className="card stack">
            <h2>2. Sync policies</h2>
            <p className="muted">
              {notion?.external_workspace_name
                ? `Connected to “${notion.external_workspace_name}”. `
                : "Notion is connected. "}
              Sync all shared policy pages, then wait here until it finishes.
            </p>

            {syncInProgress && (
              <div className="banner banner-wait" role="status" aria-live="polite">
                <div className="sync-wait-row">
                  <span className="sync-spinner" aria-hidden />
                  <div>
                    <strong>Syncing your policies…</strong>
                    <p className="muted" style={{ margin: "0.35rem 0 0" }}>
                      This can take a few minutes. Keep this page open until sync completes.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {displayJob?.status === "failed" && (
              <p className="muted">{displayJob.error || "Sync failed. Please try again."}</p>
            )}

            <button
              className="button"
              type="button"
              onClick={handleIngest}
              disabled={syncInProgress || !notion}
            >
              {syncInProgress
                ? "Syncing…"
                : displayJob?.status === "failed"
                  ? "Try again"
                  : displayJob
                    ? "Sync again"
                    : "Start sync"}
            </button>
          </section>
        )}

        {step === 3 && (
          <section className="card stack">
            <div className="banner banner-ok">
              <strong>Sync complete</strong>
              <p style={{ margin: "0.4rem 0 0" }}>
                {docCount != null
                  ? `${docCount} policy document${docCount === 1 ? "" : "s"} ready. You can ask questions now.`
                  : "Your policies are ready. You can ask questions now."}
              </p>
            </div>

            <button
              className="button"
              type="button"
              onClick={() => router.push("/chat")}
              disabled={!isSetupComplete(effectiveMe)}
            >
              Go to Ask →
            </button>

            <div className="invite-section">
              <h2>Invite your team</h2>
              <p className="muted">Optional — send a sign-in link by email.</p>
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
