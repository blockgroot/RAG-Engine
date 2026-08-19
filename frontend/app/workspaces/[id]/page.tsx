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

// GitHub is now offered per workspace (it used to be org-level only). A
// workspace owner connects their own installation, so the workspace's Code
// answers come from that installation alone -- never the org-wide one, and a
// workspace with no GitHub connection gets the fallback rather than the org's
// repos. That no-fallback scoping is what makes this safe; see
// tests/test_github_workspace_scope.py.
const PROVIDERS: ("notion" | "google" | "github" | "slack")[] = [
  "notion",
  "google",
  "github",
  "slack",
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
  /*
   * See the identical field in app/admin/connections/page.tsx: an empty
   * `connections` is indistinguishable from "nothing is connected", so the
   * cards would offer Connect for a source this space already has — inviting a
   * pointless fresh OAuth grant off a screen showing the wrong state.
   */
  const [loadingConnections, setLoadingConnections] = useState(true);
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
          if (c.provider === "slack" && !c.source_config?.channel_ids?.length) return;
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
        // Don't auto-check on load — only the Check button triggers a
        // change-check.
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

  // After GitHub/Notion/Drive OAuth, the API redirects here with ?connected=
  // (success) or ?connect_error= (refused — e.g. same GitHub install as Company).
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

    // GitHub finished on GitHub's side but its redirect could not complete the
    // link (an install/setup redirect carries no authorization code, and `state`
    // is not guaranteed to survive it). Not a conflict and not an error the user
    // caused — the App is installed now, so one more Connect click finishes it
    // against the existing installation.
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
        // Same as above — no auto-check, wait for the Check button.
      })
      .catch(() => {});
    router.replace(`/workspaces/${workspaceId}`, { scroll: false });
  }, [me, searchParams, workspaceId, refreshWorkspace, refreshChanges, router]);

  useEffect(() => {
    if (!me) return;
    function onFocus() {
      // Refreshing the workspace itself is one cheap query — always do it, so
      // returning to the tab reflects a sync that finished elsewhere. The
      // source change-check stays purely manual — only the Check button
      // triggers it.
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
          // This sync just changed what's ingested for this workspace —
          // cached suggestion chips (document titles) would otherwise keep
          // showing the pre-sync title set until a hard refresh.
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

  // `workspace` must be loaded before rendering, not just `me`.
  //
  // Everything below derives from it — `isOwner`, `docsReady`, the title — and
  // every one of those falls back to a *confident wrong answer* while it is
  // still null: `workspace?.role === "owner"` is false, so the OWNER of a space
  // was shown the member view ("Only the owner can connect or change documents
  // for this space", "Waiting on the owner to connect a source") plus a "…"
  // title and an empty member list, until the request landed. On a slow request
  // that lasted 10-15s and looked like a real answer rather than a pending one.
  //
  // `isOwner === false` cannot distinguish "you are not the owner" from "we do
  // not know yet", so the fix is to not render the branch at all until we know.
  // `notFound` is checked first, so a genuinely inaccessible workspace still
  // gets its own message rather than spinning forever.
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
                  ? "Connect Notion, Drive, Slack, or GitHub below. Docs and Slack need one sync to finish; GitHub unlocks Ask as soon as it’s linked."
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
                <span className="people-card-actions" style={{ width: "auto", marginTop: 0 }}>
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
                  {isOwner && m.role === "member" && (
                    <button
                      type="button"
                      className="button button-secondary"
                      disabled={removeMemberBusy === m.user_id}
                      onClick={() => handleRemoveMember(m.user_id, m.email)}
                      title="Removes them from this space only — their Handbook account is untouched."
                    >
                      {removeMemberBusy === m.user_id ? "…" : "Remove"}
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
            {loadingConnections &&
              // Placeholders, not Connect cards — this space's real sources are
              // still unknown at this point.
              PROVIDERS.map((provider) => (
                <div key={provider} className="studio-skeleton source-skeleton" aria-hidden />
              ))}
            {!loadingConnections &&
              PROVIDERS.map((provider) => {
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
                    // Covers GitHub's "Refresh list" (repo set changed) and a
                    // Drive folder change — both change what the suggestion
                    // chips should be built from.
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
          </section>
        ) : (
          <p className="muted">Only the owner can connect or change documents for this space.</p>
        )}

        {isOwner && (
          <section className="panel stack" style={{ borderColor: "var(--warn, #b45309)" }}>
            <div className="panel-head">
              <div>
                <h2>Delete this space</h2>
                <p className="muted" style={{ marginTop: "0.35rem" }}>
                  Permanently removes this space, its members’ access here, connected sources,
                  and indexed documents for this space only — not company-wide documents.
                </p>
              </div>
            </div>
            <button
              type="button"
              className="button button-secondary"
              disabled={deletingSpace}
              onClick={() => void handleDeleteSpace()}
              style={{ width: "fit-content" }}
            >
              {deletingSpace ? "Deleting…" : "Delete space"}
            </button>
          </section>
        )}
      </main>
    </AppShell>
  );
}
