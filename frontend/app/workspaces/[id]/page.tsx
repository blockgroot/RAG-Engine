"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { ConnectionCard } from "@/components/ConnectionCard";
import { useMe } from "@/lib/useMe";
import {
  api,
  ConnectionRecord,
  JobRecord,
  SyncChanges,
  WorkspaceDetail,
  WorkspaceMemberRecord,
} from "@/lib/api";
import { ACTIVE_JOB_STATUSES, useJobPolling } from "@/lib/jobPoll";

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
    return `Sync complete — ${docCount} page${
      docCount === 1 ? "" : "s"
    } updated. Ask can use this workspace's content now.`;
  }
  return "Sync complete — this workspace already matched the source. Nothing needed rewriting.";
}

export default function WorkspaceDetailPage() {
  const params = useParams<{ id: string }>();
  const workspaceId = params.id;
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
  const [pollToken, setPollToken] = useState(0);
  const [watchedJobId, setWatchedJobId] = useState<string | null>(null);

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviting, setInviting] = useState(false);
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

  const refreshChanges = useCallback(
    async (list: ConnectionRecord[]) => {
      if (list.length === 0) return;
      setChecking(true);
      const next: Record<string, SyncChanges> = {};
      await Promise.all(
        list.map(async (c) => {
          if (c.provider === "google" && !c.source_config?.folder_id) return;
          try {
            next[c.id] = await api.checkWorkspaceConnectionChanges(workspaceId, c.id);
          } catch {
            // ignore transient source errors
          }
        })
      );
      setChangesById((prev) => ({ ...prev, ...next }));
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
          refreshChanges(connections);
          void refreshWorkspace();
          requestAnimationFrame(() => {
            bannerRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
          });
        } else if (curr === "failed") {
          setError(job.error || "Update failed. Please try again.");
          setMessage(null);
        }
      }
      prevStatuses.current[connectionId] = curr;
    }
  }, [jobs, connections, refreshChanges, refreshWorkspace]);

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

  async function handleUpdate(connectionId: string) {
    const latest = latestJobByConnection(jobs)[connectionId];
    if (latest && ACTIVE_STATUSES.has(latest.status)) return;
    setError(null);
    setMessage("Updating changed content… Keep this page open — we'll confirm when it's done.");
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
          <Link href="/workspaces" className="button-secondary" style={{ width: "fit-content" }}>
            Back to My Workspaces
          </Link>
        </main>
      </AppShell>
    );
  }

  const isOwner = workspace?.role === "owner";
  const lastJobs = latestJobByConnection(jobs);
  const readyToAsk = Boolean(workspace?.ready_to_ask);

  return (
    <AppShell me={me} variant="app">
      <main className="page-wide stack">
        <div>
          <p className="eyebrow">Workspace</p>
          <h1>{workspace?.name || "…"}</h1>
          <p className="muted">
            Ask questions here and they&rsquo;re answered only from this workspace&rsquo;s own
            content — never the rest of your organization&rsquo;s policies.
          </p>
        </div>

        {message && (
          <div
            ref={bannerRef}
            className={
              message.startsWith("Sync complete") ? "banner banner-ok" : "banner banner-wait"
            }
            role="status"
            aria-live="polite"
          >
            {message.startsWith("Sync complete") ? (
              <>
                <strong>Update finished</strong>
                <p style={{ margin: "0.35rem 0 0" }}>{message}</p>
              </>
            ) : (
              message
            )}
          </div>
        )}
        {error && <div className="banner banner-warn">{error}</div>}

        {readyToAsk ? (
          <Link href={`/chat?workspace=${workspaceId}`} className="button" style={{ width: "fit-content" }}>
            Ask in this workspace →
          </Link>
        ) : (
          <div className="banner banner-wait" role="status">
            <strong>Ask unlocks after the first sync</strong>
            <p className="muted" style={{ margin: "0.35rem 0 0" }}>
              {workspace?.sync_in_progress
                ? "Sync in progress — this page updates automatically when content is ready."
                : isOwner
                  ? "Connect a source below and run Update policies once. Then you can ask questions here."
                  : "Waiting for the workspace owner to connect a source and finish syncing."}
            </p>
          </div>
        )}

        <div className="card stack">
          <h3 style={{ fontSize: "1.05rem" }}>Members</h3>
          <div className="stack" style={{ gap: "0.4rem" }}>
            {members.map((m) => (
              <div key={m.email} style={{ display: "flex", justifyContent: "space-between" }}>
                <span>{m.email}</span>
                <span className="badge">{m.role === "owner" ? "Owner" : "Member"}</span>
              </div>
            ))}
          </div>
          {isOwner && (
            <form onSubmit={handleInvite} className="stack" style={{ marginTop: "0.5rem" }}>
              <div className="field">
                <label htmlFor="invite-email">Invite a colleague (must already be in your org)</label>
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
                className="button-secondary"
                type="submit"
                disabled={inviting || !inviteEmail.trim()}
                style={{ width: "fit-content" }}
              >
                {inviting ? "Inviting…" : "Invite"}
              </button>
            </form>
          )}
        </div>

        {isOwner ? (
          <div className="stack">
            <h3 style={{ fontSize: "1.05rem" }}>Connect this workspace&rsquo;s source</h3>
            <p className="muted" style={{ marginTop: "-0.5rem" }}>
              Connect your own Notion page or Drive folder (e.g. meeting notes) — only you,
              as the workspace owner, can connect or change this.
            </p>
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
                  workspaceId={workspaceId}
                />
              );
            })}
          </div>
        ) : (
          <p className="muted">Only the workspace owner can connect or change its source.</p>
        )}
      </main>
    </AppShell>
  );
}
