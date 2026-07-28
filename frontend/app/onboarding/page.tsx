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
    return `Sync complete for your policy documents (${docCount} synced). You can now ask questions.`;
  }
  return "Sync complete for your policy documents. You can now ask questions.";
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
      setMessage("Notion connected — next, sync all shared policy pages (one-time setup).");
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
      setError(displayJob.error || "Sync failed. Try again — Ask stays locked until sync succeeds.");
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
    setMessage(
      "Sync started — please wait on this page until every shared policy page is ingested. Ask unlocks only when sync is fully complete."
    );
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
          <p className="muted">
            One-time setup. Ask stays locked until every shared policy document is fully synced —
            answering from a partial sync can be wrong.
          </p>
        </div>

        {message && !syncInProgress && <div className="banner banner-ok">{message}</div>}
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
            <h2>2. Sync all policy documents</h2>
            <p className="muted">
              {notion?.external_workspace_name
                ? `Connected to “${notion.external_workspace_name}”. `
                : "Notion is connected. "}
              Start sync and stay on this page until it finishes. We will not open Ask until the
              full sync succeeds.
            </p>

            {syncInProgress && (
              <div className="banner banner-wait" role="status" aria-live="polite">
                <div className="sync-wait-row">
                  <span className="sync-spinner" aria-hidden />
                  <div>
                    <strong>Please wait — syncing all policy documents</strong>
                    <p className="muted" style={{ margin: "0.35rem 0 0" }}>
                      This is a one-time setup and can take a few minutes. Keep this page open.
                      Do not ask questions yet — the portal unlocks only when every shared page
                      is ingested.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {displayJob && (
              <p className="muted" style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <JobStatusBadge status={displayJob.status} />
                {displayJob.status === "failed"
                  ? displayJob.error || "Sync failed"
                  : displayJob.status === "queued"
                    ? "Queued — starting full sync…"
                    : displayJob.status === "running"
                      ? "Ingesting shared policy pages — please wait…"
                      : displayJob.status === "succeeded"
                        ? `${displayJob.doc_count ?? "All"} documents synced`
                        : "Preparing sync…"}
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
                  ? "Waiting to start…"
                  : "Syncing all policies…"
                : displayJob?.status === "failed"
                  ? "Retry full sync"
                  : displayJob
                    ? "Sync again"
                    : "Start full sync"}
            </button>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              Sync runs in the API server. Leave this page open until you see the completion
              message — then you can invite your team or go to Ask.
            </p>
          </section>
        )}

        {step === 3 && (
          <section className="card stack">
            <div className="banner banner-ok">
              <strong>Sync complete for your policy documents</strong>
              <p style={{ margin: "0.4rem 0 0" }}>
                {docCount != null
                  ? `${docCount} document${docCount === 1 ? "" : "s"} are ready. You can now ask questions.`
                  : "Your policies are ready. You can now ask questions."}
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

            <h2>Invite your team (optional)</h2>
            <p className="muted">
              Invite adds them to your org and emails a sign-in link — they only see Ask for your
              organization.
            </p>
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
