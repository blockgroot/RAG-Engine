"use client";

import { useCallback, useEffect, useState } from "react";
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
  WorkspaceMemberRecord,
  WorkspaceRecord,
} from "@/lib/api";

const PROVIDERS: ("notion" | "google" | "github")[] = ["notion", "google", "github"];

function latestJobByConnection(jobs: JobRecord[]): Record<string, JobRecord> {
  const latest: Record<string, JobRecord> = {};
  for (const job of jobs) {
    const current = latest[job.connection_id];
    if (!current || job.created_at > current.created_at) latest[job.connection_id] = job;
  }
  return latest;
}

export default function WorkspaceDetailPage() {
  const params = useParams<{ id: string }>();
  const workspaceId = params.id;
  const { me, loading } = useMe({ enforceSetupFlow: false });

  const [workspace, setWorkspace] = useState<WorkspaceRecord | null>(null);
  const [members, setMembers] = useState<WorkspaceMemberRecord[]>([]);
  const [connections, setConnections] = useState<ConnectionRecord[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [changesById, setChangesById] = useState<Record<string, SyncChanges>>({});
  const [notFound, setNotFound] = useState(false);

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteMessage, setInviteMessage] = useState<string | null>(null);

  const refreshChanges = useCallback(
    async (list: ConnectionRecord[]) => {
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
    },
    [workspaceId]
  );

  useEffect(() => {
    if (!me) return;
    api
      .listWorkspaces()
      .then((list) => {
        const found = list.find((w) => w.id === workspaceId);
        if (!found) {
          setNotFound(true);
          return;
        }
        setWorkspace(found);
      })
      .catch(() => setNotFound(true));
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
    api
      .listWorkspaceJobs(workspaceId)
      .then(setJobs)
      .catch(() => {});
  }, [me, workspaceId, refreshChanges]);

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
    try {
      await api.triggerWorkspaceIngest(workspaceId, connectionId);
      // Simple one-shot refresh a few seconds later — this workspace UI
      // deliberately keeps job tracking lighter than the admin Sources page.
      setTimeout(() => {
        api.listWorkspaceJobs(workspaceId).then(setJobs).catch(() => {});
        refreshChanges(connections);
      }, 4000);
    } catch {
      // surfaced via the connection card's own state on next fetch
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

        <Link href={`/chat?workspace=${workspaceId}`} className="button" style={{ width: "fit-content" }}>
          Ask in this workspace →
        </Link>

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
                  onUpdate={handleUpdate}
                  onCheckAgain={connection ? () => refreshChanges(connections) : undefined}
                  onConfigSaved={(updated) => {
                    setConnections((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
                    refreshChanges([updated]);
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
