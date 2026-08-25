"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
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
import { syncPagesDetail, syncPhaseHeadline, updateCompleteMessage } from "@/lib/syncProgress";
import { invalidateSuggestionsCache } from "@/lib/suggestionsCache";

const PROVIDERS: ("notion" | "google" | "github" | "slack" | "linear")[] = [
  "notion",
  "google",
  "github",
  "slack",
  "linear",
];
const ACTIVE_STATUSES = ACTIVE_JOB_STATUSES;

function latestJobByConnection(jobs: JobRecord[]): Record<string, JobRecord> {
  const latest: Record<string, JobRecord> = {};
  for (const job of jobs) {
    const current = latest[job.connection_id];
    if (!current || job.created_at > current.created_at) latest[job.connection_id] = job;
  }
  return latest;
}

export default function WorkspaceDetailPage() {
  return (
    <Suspense
      fallback={
        <main className="page">
          <p className="muted">Loading…</p>
        </main>
      }
    >
      <WorkspaceDetailPageInner />
    </Suspense>
  );
}

function WorkspaceDetailPageInner() {
  const params = useParams<{ id: string }>();
  const workspaceId = params.id;
  const searchParams = useSearchParams();
  const router = useRouter();
  const { me, loading } = useMe({ enforceSetupFlow: false });

  const [workspace, setWorkspace] = useState<WorkspaceDetail | null>(null);
  const [members, setMembers] = useState<WorkspaceMemberRecord[]>([]);
  const [connections, setConnections] = useState<ConnectionRecord[]>([]);
  const [loadingConnections, setLoadingConnections] = useState(true);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [changesById, setChangesById] = useState<Record<string, SyncChanges>>({});
  const [checkingIds, setCheckingIds] = useState<Set<string>>(new Set());
  const [notFound, setNotFound] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reauthById, setReauthById] = useState<Record<string, boolean>>({});
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
  const [removeMemberBusy, setRemoveMemberBusy] = useState<string | null>(null);
  const [deletingSpace, setDeletingSpace] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteMessage, setInviteMessage] = useState<string | null>(null);

  const prevStatuses = useRef<Record<string, string>>({});
  const loaded = useRef(false);
  const bannerRef = useRef<HTMLDivElement | null>(null);

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
      const ids = list.map((c) => c.id);
      setCheckingIds((prev) => new Set([...prev, ...ids]));
      const next: Record<string, SyncChanges> = {};
      const failedIds: string[] = [];
      await Promise.all(
        list.map(async (c) => {
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
          if (c.provider === "slack" && !c.source_config?.channel_ids?.length) return;
          try {
            next[c.id] = await api.checkWorkspaceConnectionChanges(workspaceId, c.id);
            // A rename changes no content, so it never shows as "1 updated" —
            // but the stored labels the suggestion chips were built from are
            // now wrong, and those are cached client-side.
            if (next[c.id].renamed?.length) {
              const moved = next[c.id]
                .renamed!.map((r) => `#${r.from} → #${r.to}`)
                .join(", ");
              invalidateSuggestionsCache(workspaceId);
              setMessage(`Channel renamed in Slack (${moved}). Labels updated — no re-sync needed.`);
            }
            setReauthById((prev) => ({ ...prev, [c.id]: false }));
            setConnections((prev) =>
              prev.map((row) =>
                row.id === c.id ? { ...row, needs_reauth: false, reauth_reason: null } : row
              )
            );
          } catch (err) {
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
      setCheckingIds((prev) => {
        const remaining = new Set(prev);
        for (const id of ids) remaining.delete(id);
        return remaining;
      });
      if (gen !== changesGen.current) return;
      setChangesById((prev) => {
        const merged = { ...prev, ...next };
        for (const id of failedIds) delete merged[id];
        return merged;
      });
    },
    [workspaceId]
  );

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
      })
      .catch(() => {})
      .finally(() => setLoadingConnections(false));
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
    const connectError = searchParams.get("connect_error");
    if (connectError === "github_same_install" || connectError === "github_install_in_use") {
      setError(null);
      setConnectNotice({
        title:
          connectError === "github_install_in_use"
            ? "That GitHub account is already linked elsewhere"
            : "This space can’t reuse the company GitHub connection",
        why:
          connectError === "github_install_in_use"
            ? "Another space (or Company → Sources) already uses that GitHub App install. Company Code and space Code must stay on different accounts."
            : "Company → Sources already uses that GitHub account. If this space reused it, company Code and space Code would show the same repos.",
        options: [
          "Choose a different GitHub account on the picker (Organization for company, personal for this space).",
          "Or disconnect GitHub from the other Folio surface first, then connect it here.",
          "Or skip GitHub here and ask from the main Ask → Code tab instead.",
        ],
      });
      router.replace(`/workspaces/${workspaceId}`, { scroll: false });
      return;
    }

    if (connectError === "github_finish_connect") {
      setError(null);
      setConnectNotice({
        title: "Almost there — finish connecting GitHub",
        why:
          "GitHub sent you back without the details needed to link the account. " +
          "The app is installed on your GitHub, so connecting once more completes it.",
        options: [
          "Click “Connect personal account” below one more time.",
          "You should not have to install anything again — GitHub will remember.",
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
            : connected === "slack"
              ? "Slack"
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
      })
      .catch(() => {});
    router.replace(`/workspaces/${workspaceId}`, { scroll: false });
  }, [me, searchParams, workspaceId, refreshWorkspace, refreshChanges, router]);

  useEffect(() => {
    if (!me) return;
    function onFocus() {
      void refreshWorkspace();
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [me, refreshWorkspace]);

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
      if (ACTIVE_STATUSES.has(curr)) {
        setMessage(`${syncPhaseHeadline(job)} ${syncPagesDetail(job)}`);
        setError(null);
      } else if (prev && ACTIVE_STATUSES.has(prev) && !ACTIVE_STATUSES.has(curr)) {
        if (curr === "succeeded") {
          setMessage(updateCompleteMessage(job));
          setError(null);
          setChangesById((prev) => ({
            ...prev,
            [connectionId]: clearedSyncChanges(connectionId),
          }));
          void refreshWorkspace();
          invalidateSuggestionsCache(workspaceId);
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
  }, [jobs, connections, refreshWorkspace]);


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

  async function handleRemoveMember(userId: string, email: string) {
    if (removeMemberBusy) return;
    if (!window.confirm(`Remove ${email} from this space? Their Handbook account is untouched.`)) return;
    setRemoveMemberBusy(userId);
    setInviteError(null);
    setInviteMessage(null);
    try {
      await api.removeWorkspaceMember(workspaceId, userId);
      setInviteMessage(`Removed ${email} from this space.`);
      setMembers((prev) => prev.filter((m) => m.user_id !== userId));
    } catch (err) {
      setInviteError(err instanceof Error ? err.message : "Could not remove that member.");
    } finally {
      setRemoveMemberBusy(null);
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
    changesGen.current += 1;
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


  async function handleDeleteSpace() {
    if (deletingSpace || !workspace) return;
    const name = workspace.name;
    if (
      !window.confirm(
        `Delete “${name}” permanently? Everyone loses access, and this space’s documents, connections, and chats are removed. Company-wide documents are not affected. This cannot be undone.`
      )
    ) {
      return;
    }
    setDeletingSpace(true);
    setError(null);
    try {
      await api.deleteWorkspace(workspaceId);
      router.push("/workspaces");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete this space.");
      setDeletingSpace(false);
    }
  }

  if (loading || !me || (!workspace && !notFound)) {
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
  const canAsk = docsReady || githubReady;

  return (
    <AppShell me={me} variant="app">
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Space"
          title={workspace?.name || "…"}
          description="Responses are drawn exclusively from this space’s connected documents and repositories, not the organization-wide sources."
          scene="spaces"
          meta={
            <>
              <span className="studio-chip">{workspace?.role === "owner" ? "Owner" : "Member"}</span>
              <span className="studio-chip">{members.length} people</span>
              {canAsk ? (
                <span className="studio-chip studio-chip-ok">Ready to ask</span>
              ) : (
                <span className="studio-chip studio-chip-warn">Not ready</span>
              )}
            </>
          }
          actions={
            canAsk ? (
              <Link href={`/workspaces/${workspaceId}/ask`} className="button">
                Ask in this space
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
                  ? "Connect Notion, Drive, Slack, or GitHub below. Docs and Slack need one sync to finish; GitHub unlocks Ask as soon as it’s linked."
                  : "Waiting on the owner to connect a source."}
            </p>
          </div>
        )}

        <div className="people-layout">
          {isOwner && (
            <section className="studio-panel invite-panel" aria-labelledby="space-invite-title">
              <div className="studio-panel-glow" aria-hidden />
              <div className="studio-section-head">
                <h2 id="space-invite-title">Invite someone</h2>
                <p className="muted">They must already be in this company — this only adds them to the room.</p>
              </div>
              <form onSubmit={handleInvite} className="invite-form">
                <div className="field">
                  <label htmlFor="invite-email">Work email</label>
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
                {inviteError && (
                  <div className="banner banner-warn" role="alert">
                    {inviteError}
                  </div>
                )}
                {inviteMessage && (
                  <div className="banner banner-ok" role="status">
                    {inviteMessage}
                  </div>
                )}
                <button
                  className="button"
                  type="submit"
                  disabled={inviting || !inviteEmail.trim()}
                >
                  {inviting ? "Sending…" : "Send invite"}
                </button>
              </form>
            </section>
          )}

          <section className="roster-board" aria-labelledby="space-people-title">
            <div className="studio-section-head roster-board-head">
              <h2 id="space-people-title">People in this space</h2>
              <p className="muted">
                {isOwner
                  ? "Everyone who can ask here."
                  : "Everyone who can ask here. The owner manages invites and sources."}
              </p>
            </div>
            <div className="roster-scroll">
            {members.length === 0 ? (
              <div className="studio-empty">
                <div className="studio-empty-mark" aria-hidden />
                <h3>No one here yet</h3>
                <p className="muted">
                  {isOwner ? "Invite a teammate to share this room." : "Waiting on the owner to add people."}
                </p>
              </div>
            ) : (
              <ul className="people-grid">
                {members.map((m, i) => (
                  <li
                    key={m.user_id || m.email}
                    className="people-card"
                    style={{ animationDelay: `${0.08 + i * 0.05}s` }}
                  >
                    <span className="people-avatar" aria-hidden>
                      {(m.email || "?").trim().charAt(0).toUpperCase()}
                    </span>
                    <div className="people-card-copy">
                      <strong>{m.email}</strong>
                      <span className="muted">
                        Joined {new Date(m.joined_at).toLocaleDateString()}
                      </span>
                    </div>
                    <div className="people-card-meta">
                    <span className="badge">{m.role === "owner" ? "Owner" : "Member"}</span>
                    {isOwner && m.role === "member" ? (
                      <div className="people-card-actions">
                        <button
                          type="button"
                          className="button button-secondary"
                          disabled={makeOwnerBusy === m.user_id}
                          onClick={() => handleMakeOwner(m.user_id, m.email)}
                        >
                          {makeOwnerBusy === m.user_id ? "…" : "Make owner"}
                        </button>
                        <button
                          type="button"
                          className="button button-secondary"
                          disabled={removeMemberBusy === m.user_id}
                          onClick={() => handleRemoveMember(m.user_id, m.email)}
                          title="Removes them from this space only — their Handbook account is untouched."
                        >
                          {removeMemberBusy === m.user_id ? "…" : "Remove"}
                        </button>
                      </div>
                    ) : (
                      <div className="people-card-actions" aria-hidden />
                    )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
            </div>
          </section>
        </div>

        {isOwner && (
          <section className="studio-section" aria-label="Sources for this space">
            <div className="studio-section-head">
              <h2>Sources for this space</h2>
              <p className="muted">
                Documents sync on a schedule; GitHub is queried live. Answers stay scoped to this room.
              </p>
            </div>
            <div className="source-bento">
              {loadingConnections
                ? PROVIDERS.map((provider) => (
                    <div key={provider} className="studio-skeleton source-skeleton" aria-hidden />
                  ))
                : PROVIDERS.map((provider) => {
                    const connection = connections.find((c) => c.provider === provider);
                    return (
                      <ConnectionCard
                        key={provider}
                        provider={provider}
                        connection={connection}
                        lastJob={connection ? lastJobs[connection.id] : undefined}
                        changes={connection ? changesById[connection.id] : null}
                        checkingChanges={connection ? checkingIds.has(connection.id) : false}
                        onUpdate={handleUpdate}
                        onCheckAgain={connection ? () => refreshChanges([connection]) : undefined}
                        onConfigSaved={(updated) => {
                          setConnections((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
                          refreshChanges([updated]);
                          invalidateSuggestionsCache(workspaceId);
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
                          invalidateSuggestionsCache(workspaceId);
                          void refreshWorkspace();
                        }}
                        needsReauth={Boolean(connection && (connection.needs_reauth || reauthById[connection.id]))}
                        onNeedsReauth={(needed) => {
                          if (!connection) return;
                          setReauthById((prev) => ({ ...prev, [connection.id]: needed }));
                        }}
                        workspaceId={workspaceId}
                        onMembersInvited={async () => {
                          const updated = await api.listWorkspaceMembers(workspaceId);
                          setMembers(updated);
                        }}
                      />
                    );
                  })}
            </div>
          </section>
        )}

        {isOwner && (
          <section className="studio-panel" aria-labelledby="delete-space-title">
            <div className="studio-section-head">
              <h2 id="delete-space-title">Delete this space</h2>
              <p className="muted">
                Permanently removes this space, its members’ access here, connected sources,
                and indexed documents for this space only — not company-wide documents.
              </p>
            </div>
            <button
              type="button"
              className="button button-secondary"
              disabled={deletingSpace}
              onClick={() => void handleDeleteSpace()}
            >
              {deletingSpace ? "Deleting…" : "Delete space"}
            </button>
          </section>
        )}
      </main>
    </AppShell>
  );
}
