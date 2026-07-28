"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ConnectionCard } from "@/components/ConnectionCard";
import { useMe } from "@/lib/useMe";
import { api, ConnectionRecord, JobRecord, SyncChanges } from "@/lib/api";

const PROVIDERS: ("notion" | "google" | "github")[] = ["notion", "google", "github"];
const ACTIVE_STATUSES = new Set(["queued", "running"]);
const POLL_MS = 2500;

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
  const { me, loading } = useMe({ requireAdmin: true });
  const [connections, setConnections] = useState<ConnectionRecord[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [changesById, setChangesById] = useState<Record<string, SyncChanges>>({});
  const [checking, setChecking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const prevStatuses = useRef<Record<string, string>>({});
  const loaded = useRef(false);

  const refreshChanges = useCallback(async (list: ConnectionRecord[]) => {
    setChecking(true);
    const next: Record<string, SyncChanges> = {};
    await Promise.all(
      list.map(async (c) => {
        try {
          next[c.id] = await api.checkConnectionChanges(c.id);
        } catch {
          // Notion blip — leave prior state; don't block the page.
        }
      })
    );
    setChangesById(next);
    setChecking(false);
  }, []);

  useEffect(() => {
    if (!me || loaded.current) return;
    loaded.current = true;
    api.listConnections().then((list) => {
      setConnections(list);
      refreshChanges(list);
    });
    api.listJobs().then(setJobs);
  }, [me, refreshChanges]);

  useEffect(() => {
    if (!me) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      const list = await api.listJobs().catch(() => null);
      if (cancelled || !list) return;
      setJobs(list);
      if (list.some((j) => ACTIVE_STATUSES.has(j.status))) {
        timer = setTimeout(tick, POLL_MS);
      }
    }

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [me?.user_id]);

  useEffect(() => {
    const latest = latestJobByConnection(jobs);
    for (const [connectionId, job] of Object.entries(latest)) {
      const prev = prevStatuses.current[connectionId];
      const curr = job.status;
      if (prev && ACTIVE_STATUSES.has(prev) && !ACTIVE_STATUSES.has(curr)) {
        if (curr === "succeeded") {
          const n = job.doc_count;
          setMessage(
            n != null && n > 0
              ? `Updated ${n} policy page${n === 1 ? "" : "s"}.`
              : "Policies are already up to date."
          );
          setError(null);
          refreshChanges(connections);
        } else if (curr === "failed") {
          setError(job.error || "Update failed.");
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
    setMessage("Updating changed policies…");
    try {
      await api.triggerIngest(connectionId);
      const list = await api.listJobs();
      setJobs(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the update.");
      setMessage(null);
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
        <div>
          <p className="eyebrow">Admin</p>
          <h1>Data sources</h1>
          <p className="muted">
            Connected workspaces stay in sync — we only refresh pages that changed.
          </p>
        </div>
        {message && <div className="banner banner-ok">{message}</div>}
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
              />
            );
          })}
        </div>
      </main>
    </AppShell>
  );
}
