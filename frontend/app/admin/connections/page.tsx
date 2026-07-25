"use client";

import { useEffect, useState } from "react";
import { Nav } from "@/components/Nav";
import { ConnectionCard } from "@/components/ConnectionCard";
import { useMe } from "@/lib/useMe";
import { api, ConnectionRecord, JobRecord } from "@/lib/api";

const PROVIDERS: ("notion" | "google" | "github")[] = ["notion", "google", "github"];
const ACTIVE_STATUSES = new Set(["queued", "running"]);

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
  const { me, loading } = useMe();
  const [connections, setConnections] = useState<ConnectionRecord[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (me) api.listConnections().then(setConnections);
  }, [me]);

  useEffect(() => {
    if (!me) return;
    let cancelled = false;
    async function poll() {
      const list = await api.listJobs();
      if (cancelled) return;
      setJobs(list);
      if (list.some((j) => ACTIVE_STATUSES.has(j.status))) {
        setTimeout(poll, 3000);
      }
    }
    poll();
    return () => {
      cancelled = true;
    };
  }, [me]);

  async function handleIngest(connectionId: string) {
    setMessage(null);
    try {
      await api.triggerIngest(connectionId);
      setMessage("Sync started — this can take a minute; status will update below.");
      setTimeout(() => api.listJobs().then(setJobs), 1500);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Could not start the sync.");
    }
  }

  if (loading) return null;

  const lastJobs = latestJobByConnection(jobs);

  return (
    <>
      <Nav me={me} />
      <main className="page-wide stack">
        <h1>Connections</h1>
        <p className="muted">Connect a source so the portal can read your company&rsquo;s policy documents.</p>
        {message && <div className="card">{message}</div>}
        <div className="stack">
          {PROVIDERS.map((provider) => (
            <ConnectionCard
              key={provider}
              provider={provider}
              connection={connections.find((c) => c.provider === provider)}
              lastJob={
                connections.find((c) => c.provider === provider) &&
                lastJobs[connections.find((c) => c.provider === provider)!.id]
              }
              onIngest={handleIngest}
            />
          ))}
        </div>
      </main>
    </>
  );
}
