"use client";

import { useEffect, useState } from "react";
import { Nav } from "@/components/Nav";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { useMe } from "@/lib/useMe";
import { api, JobRecord } from "@/lib/api";

const ACTIVE_STATUSES = new Set(["queued", "running"]);

export default function JobsPage() {
  const { me, loading } = useMe();
  const [jobs, setJobs] = useState<JobRecord[]>([]);

  useEffect(() => {
    if (!me) return;

    let cancelled = false;
    async function poll() {
      const list = await api.listJobs();
      if (!cancelled) setJobs(list);
      const stillActive = list.some((j) => ACTIVE_STATUSES.has(j.status));
      if (stillActive && !cancelled) {
        setTimeout(poll, 3000);
      }
    }
    poll();
    return () => {
      cancelled = true;
    };
  }, [me]);

  if (loading) return null;

  return (
    <>
      <Nav me={me} />
      <main className="page-wide stack">
        <h1>Ingestion jobs</h1>
        <p className="muted">Fetch → chunk → embed → store, tracked from admin trigger to completion.</p>
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Documents</th>
              <th>Started</th>
              <th>Finished</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td><JobStatusBadge status={j.status} /></td>
                <td>{j.doc_count ?? "—"}</td>
                <td>{j.started_at ? new Date(j.started_at).toLocaleString() : "—"}</td>
                <td>{j.finished_at ? new Date(j.finished_at).toLocaleString() : "—"}</td>
                <td className="muted">{j.error || "—"}</td>
              </tr>
            ))}
            {jobs.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">No ingestion jobs yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </main>
    </>
  );
}
