"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { ConnectionCard } from "@/components/ConnectionCard";
import { useMe } from "@/lib/useMe";
import { api, ConnectionRecord, JobRecord, SyncChanges } from "@/lib/api";
import { ACTIVE_JOB_STATUSES, useJobPolling } from "@/lib/jobPoll";

const PROVIDERS: ("notion" | "google" | "github")[] = ["notion", "google", "github"];
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

function updateCompleteMessage(docCount: number | null | undefined): string {
  if (docCount != null && docCount > 0) {
    return `Updated · ${docCount} page${docCount === 1 ? "" : "s"}`;
  }
  return "Already up to date";
}

export default function ConnectionsPage() {
  const { me, loading } = useMe({ requireAdmin: true });
  const [connections, setConnections] = useState<ConnectionRecord[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [changesById, setChangesById] = useState<Record<string, SyncChanges>>({});
  const [checking, setChecking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Bumped when an update starts so job polling resumes (it stops when idle). */
  const [pollToken, setPollToken] = useState(0);
  const [watchedJobId, setWatchedJobId] = useState<string | null>(null);
  const prevStatuses = useRef<Record<string, string>>({});
  const loaded = useRef(false);
  const bannerRef = useRef<HTMLDivElement | null>(null);
  const connectionsRef = useRef<ConnectionRecord[]>([]);

  const refreshChanges = useCallback(async (list: ConnectionRecord[]) => {
    if (list.length === 0) return;
    setChecking(true);
    const next: Record<string, SyncChanges> = {};
    await Promise.all(
      list.map(async (c) => {
        // GitHub has no ingestion at all -- nothing is stored, so there is no
        // "changed since last sync" to compute and the API refuses this call.
        if (c.provider === "github") return;
        // Google needs a folder before change-check works.
        if (c.provider === "google" && !c.source_config?.folder_id) return;
        try {
          next[c.id] = await api.checkConnectionChanges(c.id);
        } catch {
          // Source blip — leave prior state; don't block the page.
        }
      })
    );
    setChangesById((prev) => ({ ...prev, ...next }));
    setChecking(false);
  }, []);

  useEffect(() => {
    connectionsRef.current = connections;
  }, [connections]);

  useEffect(() => {
    if (!me || loaded.current) return;
    loaded.current = true;
    api.listConnections().then((list) => {
      setConnections(list);
      refreshChanges(list);
    });
    api.listJobs().then((list) => {
      setJobs(list);
      const active = list.filter((j) => ACTIVE_STATUSES.has(j.status));
      if (active.length === 1) setWatchedJobId(active[0].id);
      else if (active.length > 1) setWatchedJobId(null);
      if (active.length > 0) setPollToken((n) => n + 1);
    });
  }, [me, refreshChanges]);

  // Re-check when the tab is focused again (e.g. after editing Notion in another tab).
  useEffect(() => {
    if (!me) return;
    function onFocus() {
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
      if (prev && ACTIVE_STATUSES.has(prev) && !ACTIVE_STATUSES.has(curr)) {
        if (curr === "succeeded") {
          setMessage(updateCompleteMessage(job.doc_count));
          setError(null);
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

  async function handleUpdate(connectionId: string) {
    const latest = latestJobByConnection(jobs)[connectionId];
    if (latest && ACTIVE_STATUSES.has(latest.status)) return;
    setError(null);
    setMessage("Updating…");
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

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide stack">
        <PageHeader
          eyebrow="Company"
          title="Company policies"
          description="Connect Notion or Drive, then keep them in sync."
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
        <div className="stack">
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
                onCheckAgain={
                  connection ? () => refreshChanges(connections) : undefined
                }
                onConfigSaved={(updated) => {
                  setConnections((prev) =>
                    prev.map((c) => (c.id === updated.id ? updated : c))
                  );
                  refreshChanges([updated]);
                }}
              />
            );
          })}
        </div>
      </main>
    </AppShell>
  );
}
