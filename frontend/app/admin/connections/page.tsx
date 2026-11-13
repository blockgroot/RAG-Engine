"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { ConnectionCard } from "@/components/ConnectionCard";
import { OwnAppSetup } from "@/components/OwnAppSetup";
import { useMe } from "@/lib/useMe";
import { api, ApiError, ConnectionRecord, ConnectorApp, JobRecord, SyncChanges } from "@/lib/api";
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
    if (!current || job.created_at > current.created_at) {
      latest[job.connection_id] = job;
    }
  }
  return latest;
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
  const [apps, setApps] = useState<{ google: ConnectorApp; slack: ConnectorApp } | null>(null);
  const [loadingConnections, setLoadingConnections] = useState(true);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [changesById, setChangesById] = useState<Record<string, SyncChanges>>({});
  const [checkingIds, setCheckingIds] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reauthById, setReauthById] = useState<Record<string, boolean>>({});
  const [pollToken, setPollToken] = useState(0);
  const [watchedJobId, setWatchedJobId] = useState<string | null>(null);
  const prevStatuses = useRef<Record<string, string>>({});
  const loaded = useRef(false);
  const bannerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const connectError = searchParams.get("connect_error");
    if (!connectError) return;
    if (connectError === "github_same_install" || connectError === "github_install_in_use") {
      setError(
        "That GitHub account is already linked to a space. Company Sources and each space must use different GitHub accounts — pick another on the chooser, or disconnect it from the space first."
      );
    } else if (connectError === "github_finish_connect") {
      setError(
        "Almost there — GitHub sent you back without the details needed to link the account. The app is installed on your GitHub, so click Connect company account once more to finish. You should not need to install anything again."
      );
    } else {
      setError("Could not finish connecting GitHub. Try again.");
    }
    router.replace("/admin/connections", { scroll: false });
  }, [searchParams, router]);


  const changesGen = useRef(0);

  const refreshChanges = useCallback(async (list: ConnectionRecord[]) => {
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
        if (c.provider === "google" && !c.source_config?.folder_id) return;
        if (c.provider === "slack" && !c.source_config?.channel_ids?.length) return;
        try {
          next[c.id] = await api.checkConnectionChanges(c.id);
          // A rename changes no content but stales the cached chip labels.
          if (next[c.id].renamed?.length) {
            const moved = next[c.id]
              .renamed!.map((r) => `#${r.from} → #${r.to}`)
              .join(", ");
            invalidateSuggestionsCache(null);
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
  }, []);

  /** Re-read jobs and adopt running ones: the server queues the first ingest
   *  itself (`autosync.sync_now`). */
  const adoptJobs = useCallback(async () => {
    const list = await api.listJobs().catch(() => null);
    if (!list) return;
    setJobs(list);
    const active = list.filter((j) => ACTIVE_STATUSES.has(j.status));
    setWatchedJobId(active.length === 1 ? active[0].id : null);
    if (active.length > 0) setPollToken((n) => n + 1);
  }, []);

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
      })
      .catch(() => {
        /* useMe owns the auth redirect; leave the cards in their unknown state */
      })
      .finally(() => setLoadingConnections(false));
    api
      .listConnectorApps()
      .then(setApps)
      .catch(() => {
        /* the setup cards stay hidden; Connect explains what is missing */
      });
    void adoptJobs();
  }, [adoptJobs]);

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
        setMessage(
          `${syncPhaseHeadline(job)} ${syncPagesDetail(job, connections.find((c) => c.id === connectionId)?.provider)}`,
        );
        setError(null);
      } else if (prev && ACTIVE_STATUSES.has(prev) && !ACTIVE_STATUSES.has(curr)) {
        if (curr === "succeeded") {
          setMessage(updateCompleteMessage(job));
          setError(null);
          setChangesById((prev) => ({
            ...prev,
            [connectionId]: clearedSyncChanges(connectionId),
          }));
          invalidateSuggestionsCache(null);
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
  }, [jobs, connections]);

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
    if (p === "slack" && !c.source_config?.channel_ids?.length) return true;
    return Boolean(changesById[c.id]?.has_changes);
  }).length;

  const linked = PROVIDERS.filter((p) => connections.some((c) => c.provider === p));
  const unlinked = PROVIDERS.filter((p) => !linked.includes(p));
  const renderSource = (provider: (typeof PROVIDERS)[number]) => {
    const connection = connections.find((c) => c.provider === provider);
    return (
      <ConnectionCard
        key={provider}
        provider={provider}
        setupFirst={
          (provider === "google" || provider === "slack") &&
          Boolean(apps) &&
          !apps![provider].configured &&
          !apps![provider].server_app
        }
        connection={connection}
        lastJob={connection ? lastJobs[connection.id] : undefined}
        changes={connection ? changesById[connection.id] : null}
        checkingChanges={connection ? checkingIds.has(connection.id) : false}
        onUpdate={handleUpdate}
        onConfigSaved={(updated) => {
          setConnections((prev) =>
            prev.map((c) => (c.id === updated.id ? updated : c))
          );
          // Saving the folder/channels queues the ingest server-side.
          void adoptJobs();
          refreshChanges([updated]);
          invalidateSuggestionsCache(null);
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
          invalidateSuggestionsCache(null);
          setMessage("Disconnected. Indexed docs for that source were removed.");
        }}
        needsReauth={Boolean(connection && (connection.needs_reauth || reauthById[connection.id]))}
        onNeedsReauth={(needed) => {
          if (!connection) return;
          setReauthById((prev) => ({ ...prev, [connection.id]: needed }));
        }}
      />
    );
  };

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Company"
          title="Sources"
          description="Where Handbook reads from. Connect a tool once and it stays in sync."
          meta={
            loadingConnections ? (
              <span className="hero-facts">Loading…</span>
            ) : (
              <span className="hero-facts">
                <b>{linkedCount}</b> of {PROVIDERS.length} connected
                <span aria-hidden>·</span>
                {needsAttention > 0 ? (
                  <span className="hero-fact-warn">{needsAttention} need attention</span>
                ) : (
                  <span className="hero-fact-ok">All set</span>
                )}
              </span>
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
        {apps && (
          <section className="studio-section" aria-label="Your company's apps">
            <div className="studio-section-head">
              <h2>Your company&apos;s apps</h2>
              <p className="muted">
                Drive and Slack connect through an app your company owns. Set it up once.
              </p>
            </div>
            <div className="own-apps-grid">
              {(["google", "slack"] as const).map((provider) => (
                <OwnAppSetup
                  key={provider}
                  provider={provider}
                  app={apps[provider]}
                  onSaved={(updated) => {
                    setApps((prev) => (prev ? { ...prev, [provider]: updated } : prev));
                    // Saving asks existing connections to reconnect.
                    void api.listConnections().then(setConnections).catch(() => {});
                  }}
                />
              ))}
            </div>
          </section>
        )}
        <section className="studio-section room-sources" aria-label="Source connections">
          <div className="studio-section-head">
            <h2>Your connections</h2>
          </div>
          <div className="source-bento">
            {loadingConnections
              ? PROVIDERS.map((provider) => (
                  <div key={provider} className="studio-skeleton source-skeleton" aria-hidden />
                ))
              : (
                  <>
                    {linked.length > 0 && <p className="room-sub">Connected · {linked.length}</p>}
                    {linked.map(renderSource)}
                    {unlinked.length > 0 && <p className="room-sub">Add a source</p>}
                    <div className="room-add-grid">{unlinked.map(renderSource)}</div>
                  </>
                )}
          </div>
        </section>
      </main>
    </AppShell>
  );
}
