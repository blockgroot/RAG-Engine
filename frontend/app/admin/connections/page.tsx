"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { ConnectionCard } from "@/components/ConnectionCard";
import { useMe } from "@/lib/useMe";
import { api, ApiError, ConnectionRecord, JobRecord, SyncChanges } from "@/lib/api";
import { ACTIVE_JOB_STATUSES, useJobPolling } from "@/lib/jobPoll";
import { clearedSyncChanges } from "@/lib/syncChanges";
import { syncPagesDetail, syncPhaseHeadline } from "@/lib/syncProgress";

const PROVIDERS: ("notion" | "google" | "github")[] = ["notion", "google", "github"];
const ACTIVE_STATUSES = ACTIVE_JOB_STATUSES;
// Minimum gap between window-focus-triggered change-checks. A Drive check walks
// the folder tree live (one Google API call per subfolder), so an unthrottled
// focus listener re-ran seconds of work every time the user alt-tabbed.
const CHANGE_CHECK_MIN_INTERVAL_MS = 60_000;

function latestJobByConnection(jobs: JobRecord[]): Record<string, JobRecord> {
  const latest: Record<string, JobRecord> = {};
  for (const job of jobs) {
    const current = latest[job.connection_id];
    if (!current || job.created_at > current.created_at) {
      latest[job.connection_id] = job;
    }
  }
  return latest;
}

function updateCompleteMessage(docCount: number | null | undefined): string {
  if (docCount != null && docCount > 0) {
    return `Updated · ${docCount} page${docCount === 1 ? "" : "s"}`;
  }
  return "Already up to date";
}

export default function ConnectionsPage() {
  return (
    <Suspense
      fallback={
        <main className="page">
          <p className="muted">Loading…</p>
        </main>
      }
    >
      <ConnectionsPageInner />
    </Suspense>
  );
}

function ConnectionsPageInner() {
  const { me, loading } = useMe({ requireAdmin: true });
  const searchParams = useSearchParams();
  const router = useRouter();
  const [connections, setConnections] = useState<ConnectionRecord[]>([]);
  /*
   * Whether the connections list has been fetched yet.
   *
   * `connections` starting as `[]` is indistinguishable from "this org has
   * connected nothing", so the cards used to render "Not linked yet" + a
   * Connect button for an already-connected provider until the fetch landed.
   * That is not just a cosmetic flash: clicking Connect on an already-connected
   * source starts a fresh OAuth grant, which is a destructive-ish action the
   * user was invited to take by a screen showing the wrong state. Same class of
   * bug as the workspace owner briefly seeing the member-only view — an unknown
   * value must not be rendered as `false`.
   */
  const [loadingConnections, setLoadingConnections] = useState(true);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [changesById, setChangesById] = useState<Record<string, SyncChanges>>({});
  const [checking, setChecking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reauthById, setReauthById] = useState<Record<string, boolean>>({});
  /** Bumped when an update starts so job polling resumes (it stops when idle). */
  const [pollToken, setPollToken] = useState(0);
  const [watchedJobId, setWatchedJobId] = useState<string | null>(null);
  const prevStatuses = useRef<Record<string, string>>({});
  const loaded = useRef(false);
  const bannerRef = useRef<HTMLDivElement | null>(null);
  const connectionsRef = useRef<ConnectionRecord[]>([]);

  useEffect(() => {
    const connectError = searchParams.get("connect_error");
    if (!connectError) return;
    if (connectError === "github_same_install" || connectError === "github_install_in_use") {
      setError(
        "That GitHub account is already linked to a space. Company Sources and each space must use different GitHub accounts — pick another on the chooser, or disconnect it from the space first."
      );
    } else {
      setError("Could not finish connecting GitHub. Try again.");
    }
    router.replace("/admin/connections", { scroll: false });
  }, [searchParams, router]);


  const changesGen = useRef(0);
  // When the last change-check actually ran, so the focus listener can skip one
  // that just happened (see CHANGE_CHECK_MIN_INTERVAL_MS).
  const lastChangeCheckAt = useRef(0);

  const refreshChanges = useCallback(async (list: ConnectionRecord[]) => {
    if (list.length === 0) return;
    const gen = ++changesGen.current;
    lastChangeCheckAt.current = Date.now();
    setChecking(true);
    const next: Record<string, SyncChanges> = {};
    const failedIds: string[] = [];
    await Promise.all(
      list.map(async (c) => {
        // GitHub has no change-check; probe credential mint on load so a dead
        // install shows Reconnect without waiting for Refresh list.
        if (c.provider === "github") {
          try {
            await api.checkConnectionHealth(c.id);
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
        // Google needs a folder before change-check works.
        if (c.provider === "google" && !c.source_config?.folder_id) return;
        try {
          next[c.id] = await api.checkConnectionChanges(c.id);
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
  }, []);

  useEffect(() => {
    connectionsRef.current = connections;
  }, [connections]);

  // Fired on mount rather than gated on `me`, so the session lookup and these
  // loads run CONCURRENTLY instead of as a waterfall — the wait before the cards
  // know their real state was two sequential round trips, not one. Both are
  // authenticated by the same cookie, so there is nothing to wait for; an
  // unauthenticated/non-admin caller just gets a 401/403 here and useMe's guard
  // does the redirect.
  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    api
      .listConnections()
      .then((list) => {
        setConnections(list);
        setReauthById(
          Object.fromEntries(list.filter((c) => c.needs_reauth).map((c) => [c.id, true]))
        );
        refreshChanges(list);
      })
      .catch(() => {
        /* useMe owns the auth redirect; leave the cards in their unknown state */
      })
      .finally(() => setLoadingConnections(false));
    api.listJobs().then((list) => {
      setJobs(list);
      const active = list.filter((j) => ACTIVE_STATUSES.has(j.status));
      if (active.length === 1) setWatchedJobId(active[0].id);
      else if (active.length > 1) setWatchedJobId(null);
      if (active.length > 0) setPollToken((n) => n + 1);
    }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-check when the tab is focused again (e.g. after editing Notion in another
  // tab) — but throttled. A Drive change-check walks the folder tree live, one
  // Google API call per subfolder, so an unthrottled listener put every card
  // back into "Checking…" for seconds on every alt-tab. The explicit
  // "Check again" button still forces one immediately.
  useEffect(() => {
    if (!me) return;
    function onFocus() {
      if (Date.now() - lastChangeCheckAt.current < CHANGE_CHECK_MIN_INTERVAL_MS) {
        return;
      }
      refreshChanges(connectionsRef.current);
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [me, refreshChanges]);

  const hasActiveJob = jobs.some((j) => ACTIVE_STATUSES.has(j.status));
  useJobPolling({
    enabled: Boolean(me) && (watchedJobId != null || hasActiveJob),
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
          setMessage(updateCompleteMessage(job.doc_count));
          setError(null);
          // Hide Update immediately — Drive change-check is slow; without this
          // a stale has_changes from before the sync keeps the button up.
          setChangesById((prev) => ({
            ...prev,
            [connectionId]: clearedSyncChanges(connectionId),
          }));
          refreshChanges(connections);
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
  }, [jobs, connections, refreshChanges]);

  const updateInFlight = useRef<Set<string>>(new Set());

  async function handleUpdate(connectionId: string) {
    const latest = latestJobByConnection(jobs)[connectionId];
    if (latest && ACTIVE_STATUSES.has(latest.status)) return;
    if (updateInFlight.current.has(connectionId)) return;
    updateInFlight.current.add(connectionId);
    setError(null);
    setMessage("Updating…");
    // Optimistic: don't keep offering Update while the job is starting.
    setChangesById((prev) => ({
      ...prev,
      [connectionId]: clearedSyncChanges(connectionId),
    }));
    try {
      const { job_id } = await api.triggerIngest(connectionId);
      setWatchedJobId(job_id);
      const job = await api.getJob(job_id).catch(() => null);
      if (job) {
        setJobs((prev) => [job, ...prev.filter((j) => j.id !== job.id)]);
      }
      setPollToken((n) => n + 1);
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

  const lastJobs = latestJobByConnection(jobs);
  const linkedCount = connections.length;
  const needsAttention = PROVIDERS.filter((p) => {
    const c = connections.find((x) => x.provider === p);
    if (!c) return true;
    if (p === "google" && !c.source_config?.folder_id) return true;
    return Boolean(changesById[c.id]?.has_changes);
  }).length;

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Company"
          title="Sources"
          description="Connect Notion, Google Drive, and company GitHub. Spaces need their own GitHub account — never the same install as here."
          scene="sources"
          meta={
            loadingConnections ? (
              // Both counts are derived from `connections`, so before it loads
              // they would confidently read "0 linked" and "3 need attention" —
              // exactly the claim that must not be made while unknown.
              <>
                <span className="studio-chip">{PROVIDERS.length} providers</span>
                <span className="studio-chip">Loading…</span>
              </>
            ) : (
              <>
                <span className="studio-chip studio-chip-ok">{linkedCount} linked</span>
                <span className="studio-chip">{PROVIDERS.length} providers</span>
                {needsAttention > 0 ? (
                  <span className="studio-chip studio-chip-warn">{needsAttention} need attention</span>
                ) : (
                  <span className="studio-chip studio-chip-ok">All set</span>
                )}
              </>
            )
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
        {error && <div className="banner banner-warn">{error}</div>}
        <section className="studio-section" aria-label="Source connections">
          <div className="studio-section-head">
            <h2>Your connections</h2>
            <p className="muted">Each source keeps its own sync boundary — never mixed across providers.</p>
          </div>
          <div className="source-bento">
            {loadingConnections
              ? PROVIDERS.map((provider) => (
                  // Placeholder, NOT a "Connect" card: until the list arrives we
                  // do not know whether this provider is linked, and offering
                  // Connect for an already-connected source invites a pointless
                  // (and confusing) fresh OAuth grant.
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
                  checkingChanges={Boolean(connection) && checking}
                  onUpdate={handleUpdate}
                  onCheckAgain={
                    connection ? () => refreshChanges(connections) : undefined
                  }
                  onConfigSaved={(updated) => {
                    setConnections((prev) =>
                      prev.map((c) => (c.id === updated.id ? updated : c))
                    );
                    refreshChanges([updated]);
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
                  }}
                  needsReauth={Boolean(connection && (connection.needs_reauth || reauthById[connection.id]))}
                  onNeedsReauth={(needed) => {
                    if (!connection) return;
                    setReauthById((prev) => ({ ...prev, [connection.id]: needed }));
                  }}
                />
              );
                })}
          </div>
        </section>
      </main>
    </AppShell>
  );
}
