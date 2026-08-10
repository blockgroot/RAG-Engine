"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { ConnectionCard } from "@/components/ConnectionCard";
import { useMe } from "@/lib/useMe";
import {
  api,
  ApiError,
  ConnectionRecord,
  JobRecord,
  SyncChanges,
  WorkspaceDetail,
  WorkspaceMemberRecord,
} from "@/lib/api";
import { ACTIVE_JOB_STATUSES, useJobPolling } from "@/lib/jobPoll";
import { clearedSyncChanges } from "@/lib/syncChanges";

// GitHub is now offered per workspace (it used to be org-level only). A
// workspace owner connects their own installation, so the workspace's Code
// answers come from that installation alone -- never the org-wide one, and a
// workspace with no GitHub connection gets the fallback rather than the org's
// repos. That no-fallback scoping is what makes this safe; see
// tests/test_github_workspace_scope.py.
const PROVIDERS: ("notion" | "google" | "github")[] = ["notion", "google", "github"];
const ACTIVE_STATUSES = ACTIVE_JOB_STATUSES;

function latestJobByConnection(jobs: JobRecord[]): Record<string, JobRecord> {
  const latest: Record<string, JobRecord> = {};
  for (const job of jobs) {
    const current = latest[job.connection_id];
    if (!current || job.created_at > current.created_at) latest[job.connection_id] = job;
  }
  return latest;
}

function updateCompleteMessage(docCount: number | null | undefined): string {
  if (docCount != null && docCount > 0) {
    return `Updated · ${docCount} page${docCount === 1 ? "" : "s"}`;
  }
  return "Already up to date";
}

export default function WorkspaceDetailPage() {
  const params = useParams<{ id: string }>();
  const workspaceId = params.id;
  const searchParams = useSearchParams();
  const router = useRouter();
  const { me, loading } = useMe({ enforceSetupFlow: false });

  const [workspace, setWorkspace] = useState<WorkspaceDetail | null>(null);
  const [members, setMembers] = useState<WorkspaceMemberRecord[]>([]);
  const [connections, setConnections] = useState<ConnectionRecord[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [changesById, setChangesById] = useState<Record<string, SyncChanges>>({});
  const [checking, setChecking] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reauthById, setReauthById] = useState<Record<string, boolean>>({});
  /** Structured OAuth refusal (not a single bulk paragraph). */
  const [connectNotice, setConnectNotice] = useState<{
    title: string;
    why: string;
    options: string[];
  } | null>(null);
  const [pollToken, setPollToken] = useState(0);
  const [watchedJobId, setWatchedJobId] = useState<string | null>(null);

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviting, setInviting] = useState(false);
  const [makeOwnerBusy, setMakeOwnerBusy] = useState<string | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteMessage, setInviteMessage] = useState<string | null>(null);

  const prevStatuses = useRef<Record<string, string>>({});
  const loaded = useRef(false);
  const bannerRef = useRef<HTMLDivElement | null>(null);
  const connectionsRef = useRef<ConnectionRecord[]>([]);

  const refreshWorkspace = useCallback(async () => {
    try {
      const detail = await api.getWorkspace(workspaceId);
      setWorkspace(detail);
      return detail;
    } catch {
      setNotFound(true);
      return null;
    }
  }, [workspaceId]);

  const changesGen = useRef(0);

  const refreshChanges = useCallback(
    async (list: ConnectionRecord[]) => {
      if (list.length === 0) return;
      const gen = ++changesGen.current;
      setChecking(true);
      const next: Record<string, SyncChanges> = {};
      const failedIds: string[] = [];
      await Promise.all(
        list.map(async (c) => {
          // GitHub has no change-check; probe credential mint instead so
          // needs_reauth is not refresh-list-only.
          if (c.provider === "github") {
            try {
              await api.checkWorkspaceConnectionHealth(workspaceId, c.id);
              setReauthById((prev) => ({ ...prev, [c.id]: false }));
              setConnections((prev) =>
                prev.map((row) =>
                  row.id === c.id ? { ...row, needs_reauth: false, reauth_reason: null } : row
                )
              );
            } catch (err) {
              if (err instanceof ApiError && err.code === "oauth_reauth_required") {
                setReauthById((prev) => ({ ...prev, [c.id]: true }));
                setConnections((prev) =>
                  prev.map((row) =>
                    row.id === c.id
                      ? { ...row, needs_reauth: true, reauth_reason: err.message }
                      : row
                  )
                );
              }
            }
            return;
          }
          if (c.provider === "google" && !c.source_config?.folder_id) return;
          try {
            next[c.id] = await api.checkWorkspaceConnectionChanges(workspaceId, c.id);
            setReauthById((prev) => ({ ...prev, [c.id]: false }));
            setConnections((prev) =>
              prev.map((row) =>
                row.id === c.id ? { ...row, needs_reauth: false, reauth_reason: null } : row
              )
            );
          } catch (err) {
            // Drop prior has_changes so a failed re-check after sync cannot
            // leave a sticky Update button.
            failedIds.push(c.id);
            if (err instanceof ApiError && err.code === "oauth_reauth_required") {
              setReauthById((prev) => ({ ...prev, [c.id]: true }));
              setConnections((prev) =>
                prev.map((row) =>
                  row.id === c.id
                    ? { ...row, needs_reauth: true, reauth_reason: err.message }
                    : row
                )
              );
            }
          }
        })
      );
      if (gen !== changesGen.current) return; // newer check won
      setChangesById((prev) => {
        const merged = { ...prev, ...next };
        for (const id of failedIds) delete merged[id];
        return merged;
      });
      setChecking(false);
    },
    [workspaceId]
  );

  useEffect(() => {
    connectionsRef.current = connections;
  }, [connections]);

  useEffect(() => {
    if (!me || loaded.current) return;
    loaded.current = true;
    refreshWorkspace().then((detail) => {
      if (!detail) return;
    });
    api
      .listWorkspaceMembers(workspaceId)
      .then(setMembers)
      .catch(() => setNotFound(true));
    api
      .listWorkspaceConnections(workspaceId)
      .then((list) => {
        setConnections(list);
        setReauthById(
          Object.fromEntries(list.filter((c) => c.needs_reauth).map((c) => [c.id, true]))
        );
        refreshChanges(list);
      })
      .catch(() => {});
    api.listWorkspaceJobs(workspaceId).then((list) => {
      setJobs(list);
      const active = list.filter((j) => ACTIVE_STATUSES.has(j.status));
      if (active.length === 1) setWatchedJobId(active[0].id);
      else if (active.length > 1) setWatchedJobId(null);
      if (active.length > 0) setPollToken((n) => n + 1);
    }).catch(() => {});
  }, [me, workspaceId, refreshChanges, refreshWorkspace]);

  // After GitHub/Notion/Drive OAuth, the API redirects here with ?connected=
  // (success) or ?connect_error= (refused — e.g. same GitHub install as Company).
  useEffect(() => {
    if (!me) return;
    const connectError = searchParams.get("connect_error");
    if (connectError === "github_same_install") {
      setError(null);
      setConnectNotice({
        title: "This space can’t use the company GitHub connection",
        why: "Company → Sources already uses that GitHub account. If this space reused it, company Code and space Code would show the same repos.",
        options: [
          "Put company GitHub on a GitHub Organization, reconnect it under Company → Sources, then connect this space with your personal account.",
          "Or disconnect GitHub from Company → Sources, then connect it only on this space.",
          "Or skip GitHub here and ask from the main Ask → Code tab instead.",
        ],
      });
      router.replace(`/workspaces/${workspaceId}`, { scroll: false });
      return;
    }

    const connected = searchParams.get("connected");
    if (!connected) return;
    const label =
      connected === "github"
        ? "GitHub"
        : connected === "google"
          ? "Google Drive"
          : connected === "notion"
            ? "Notion"
            : connected;
    setMessage(
      connected === "github"
        ? `${label} connected to this space. You and invited colleagues can ask in the Code tab — only these repos, not the company GitHub.`
        : `${label} connected to this space.`
    );
    void refreshWorkspace();
    api
      .listWorkspaceConnections(workspaceId)
      .then((list) => {
        setConnections(list);
        setReauthById(
          Object.fromEntries(list.filter((c) => c.needs_reauth).map((c) => [c.id, true]))
        );
        refreshChanges(list);
      })
      .catch(() => {});
    router.replace(`/workspaces/${workspaceId}`, { scroll: false });
  }, [me, searchParams, workspaceId, refreshWorkspace, refreshChanges, router]);

  useEffect(() => {
    if (!me) return;
    function onFocus() {
      refreshChanges(connectionsRef.current);
      void refreshWorkspace();
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [me, refreshChanges, refreshWorkspace]);

  const hasActiveJob = jobs.some((j) => ACTIVE_STATUSES.has(j.status));
  useJobPolling({
    enabled: Boolean(me) && (watchedJobId != null || hasActiveJob),
    jobId: watchedJobId,
    workspaceId,
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
      const stillActive = fetched.some((j) => ACTIVE_STATUSES.has(j.status));
      if (!stillActive) setWatchedJobId(null);
      return stillActive;
    },
  });

  useEffect(() => {
    const latest = latestJobByConnection(jobs);
    for (const [connectionId, job] of Object.entries(latest)) {
      const prev = prevStatuses.current[connectionId];
      const curr = job.status;
      if (prev && ACTIVE_STATUSES.has(prev) && !ACTIVE_STATUSES.has(curr)) {
        if (curr === "succeeded") {
          setMessage(updateCompleteMessage(job.doc_count));
          setError(null);
          setChangesById((prev) => ({
            ...prev,
            [connectionId]: clearedSyncChanges(connectionId),
          }));
          refreshChanges(connections);
          void refreshWorkspace();
          requestAnimationFrame(() => {
            bannerRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
          });
        } else if (curr === "failed") {
          setError(job.error || "Update didn’t finish. Please try again.");
          setMessage(null);
        }
      }
      prevStatuses.current[connectionId] = curr;
    }
  }, [jobs, connections, refreshChanges, refreshWorkspace]);


  async function handleMakeOwner(userId: string, email: string) {
    if (makeOwnerBusy) return;
    if (!window.confirm(`Make ${email} an owner of this space?`)) return;
    setMakeOwnerBusy(userId);
    setInviteError(null);
    setInviteMessage(null);
    try {
      await api.makeWorkspaceOwner(workspaceId, userId);
      setInviteMessage(`${email} is now an owner.`);
      const updated = await api.listWorkspaceMembers(workspaceId);
      setMembers(updated);
    } catch (err) {
      setInviteError(err instanceof Error ? err.message : "Could not make that person an owner.");
    } finally {
      setMakeOwnerBusy(null);
    }
  }

  async function handleInvite(e: React.FormEvent) {

    e.preventDefault();
    const email = inviteEmail.trim();
    if (!email || inviting) return;
    setInviting(true);
    setInviteError(null);
    setInviteMessage(null);
    try {
      await api.inviteWorkspaceMember(workspaceId, email);
      setInviteMessage(`Invited ${email}.`);
      setInviteEmail("");
      const updated = await api.listWorkspaceMembers(workspaceId);
      setMembers(updated);
    } catch (err) {
      setInviteError(err instanceof Error ? err.message : "Could not invite that email.");
    } finally {
      setInviting(false);
    }
  }

  const updateInFlight = useRef<Set<string>>(new Set());

  async function handleUpdate(connectionId: string) {
    const latest = latestJobByConnection(jobs)[connectionId];
    if (latest && ACTIVE_STATUSES.has(latest.status)) return;
    if (updateInFlight.current.has(connectionId)) return;
    updateInFlight.current.add(connectionId);
    setError(null);
    setMessage("Updating…");
    setChangesById((prev) => ({
      ...prev,
      [connectionId]: clearedSyncChanges(connectionId),
    }));
    try {
      const { job_id } = await api.triggerWorkspaceIngest(workspaceId, connectionId);
      setWatchedJobId(job_id);
      const job = await api.getWorkspaceJob(workspaceId, job_id).catch(() => null);
      if (job) {
        setJobs((prev) => [job, ...prev.filter((j) => j.id !== job.id)]);
      }
      setPollToken((n) => n + 1);
      void refreshWorkspace();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the update.");
      setMessage(null);
      setWatchedJobId(null);
    } finally {
      updateInFlight.current.delete(connectionId);
    }
  }

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  if (notFound) {
    return (
      <AppShell me={me} variant="app">
        <main className="page stack">
          <h1>Workspace not found</h1>
          <p className="muted">
            This workspace doesn&rsquo;t exist, or you&rsquo;re not a member of it.
          </p>
          <Link href="/workspaces" className="button button-secondary" style={{ width: "fit-content" }}>
            Back to My Workspaces
          </Link>
        </main>
      </AppShell>
    );
  }

  const isOwner = workspace?.role === "owner";
  const lastJobs = latestJobByConnection(jobs);
  const docsReady = Boolean(workspace?.ready_to_ask);
  const githubReady = Boolean(workspace?.github_connected);
  // Same idea as org Ask: docs need an ingest; GitHub is live — either unlocks Ask.
  const canAsk = docsReady || githubReady;

  return (
    <AppShell me={me} variant="app">
      <main className="page-wide stack">
        <PageHeader
          eyebrow="Space"
          title={workspace?.name || "…"}
          description="Answers come only from this space’s connected docs and repos — not the company-wide sources."
          actions={
            canAsk ? (
              <Link href={`/chat?workspace=${workspaceId}`} className="button">
                Ask →
              </Link>
            ) : undefined
          }
        />

        {message && (
          <div
            ref={bannerRef}
            className={
              message.startsWith("Updating") ? "banner banner-wait" : "banner banner-ok"
            }
            role="status"
            aria-live="polite"
          >
            {message}
          </div>
        )}
        {connectNotice && (
          <div className="banner banner-warn connect-notice" role="alert">
            <p className="connect-notice-title">{connectNotice.title}</p>
            <p className="connect-notice-why">{connectNotice.why}</p>
            <p className="connect-notice-label">What you can do</p>
            <ol className="connect-notice-options">
              {connectNotice.options.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
            <button
              type="button"
              className="button button-secondary connect-notice-dismiss"
              onClick={() => setConnectNotice(null)}
            >
              Dismiss
            </button>
          </div>
        )}
        {error && <div className="banner banner-warn">{error}</div>}

        {!canAsk && (
          <div className="banner banner-wait" role="status">
            <strong>Not ready to ask yet</strong>
            <p className="muted" style={{ margin: "0.35rem 0 0" }}>
              {workspace?.sync_in_progress
                ? "Still importing documents…"
                : isOwner
                  ? "Connect Notion, Drive, or GitHub below. For docs, sync once; for GitHub, Ask unlocks as soon as it’s linked."
                  : "Waiting on the owner to connect a source."}
            </p>
          </div>
        )}

        <section className="panel stack">
          <div className="panel-head">
            <h2>People in this space</h2>
          </div>
          <div className="stack" style={{ gap: "0.45rem" }}>
            {members.map((m) => (
              <div key={m.user_id || m.email} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "1rem" }}>
                <span>{m.email}</span>
                <span style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <span className="badge">{m.role === "owner" ? "Owner" : "Member"}</span>
                  {isOwner && m.role === "member" && (
                    <button
                      type="button"
                      className="button button-secondary"
                      disabled={makeOwnerBusy === m.user_id}
                      onClick={() => handleMakeOwner(m.user_id, m.email)}
                    >
                      {makeOwnerBusy === m.user_id ? "…" : "Make owner"}
                    </button>
                  )}
                </span>
              </div>
            ))}
          </div>
          {isOwner && (
            <form onSubmit={handleInvite} className="stack" style={{ marginTop: "0.35rem" }}>
              <div className="field">
                <label htmlFor="invite-email">Invite by email</label>
                <input
                  id="invite-email"
                  className="input"
                  type="email"
                  placeholder="colleague@yourcompany.com"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  disabled={inviting}
                />
              </div>
              {inviteError && <div className="banner banner-warn">{inviteError}</div>}
              {inviteMessage && <div className="banner banner-ok">{inviteMessage}</div>}
              <button
                className="button button-secondary"
                type="submit"
                disabled={inviting || !inviteEmail.trim()}
                style={{ width: "fit-content" }}
              >
                {inviting ? "Sending…" : "Send invite"}
              </button>
            </form>
          )}
        </section>

        {isOwner ? (
          <section className="stack">
            <div className="panel-head" style={{ marginBottom: 0 }}>
              <div>
                <h2>Sources for this space</h2>
                <p className="muted" style={{ marginTop: "0.35rem" }}>
                  Docs sync; GitHub is live. Colleagues you invite can ask here — only what you connect to this space.
                </p>
              </div>
            </div>
            {PROVIDERS.map((provider) => {
              const connection = connections.find((c) => c.provider === provider);
              return (
                <ConnectionCard
                  key={provider}
                  provider={provider}
                  connection={connection}
                  lastJob={connection ? lastJobs[connection.id] : undefined}
                  changes={connection ? changesById[connection.id] : null}
                  checkingChanges={Boolean(connection) && checking}
                  onUpdate={handleUpdate}
                  onCheckAgain={connection ? () => refreshChanges(connections) : undefined}
                  onConfigSaved={(updated) => {
                    setConnections((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
                    refreshChanges([updated]);
                    void refreshWorkspace();
                  }}
                  onDisconnected={(connectionId) => {
                    setConnections((prev) => prev.filter((c) => c.id !== connectionId));
                    setChangesById((prev) => {
                      const next = { ...prev };
                      delete next[connectionId];
                      return next;
                    });
                    setReauthById((prev) => {
                      const next = { ...prev };
                      delete next[connectionId];
                      return next;
                    });
                    setMessage("Disconnected. Indexed docs for that source were removed.");
                    void refreshWorkspace();
                  }}
                  needsReauth={Boolean(connection && (connection.needs_reauth || reauthById[connection.id]))}
                  onNeedsReauth={(needed) => {
                    if (!connection) return;
                    setReauthById((prev) => ({ ...prev, [connection.id]: needed }));
                  }}
                  workspaceId={workspaceId}
                />
              );
            })}
          </section>
        ) : (
          <p className="muted">Only the owner can connect or change documents for this space.</p>
        )}
      </main>
    </AppShell>
  );
}
