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

function updateCompleteMessage(docCount: number | null | undefined): string {
  if (docCount != null && docCount > 0) {
    return `Sync complete — ${docCount} policy page${
      docCount === 1 ? "" : "s"
    } updated to match Notion. Ask can use the new policies now.`;
  }
  return "Sync complete — your policies already matched Notion. Nothing needed rewriting.";
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
        try {
          next[c.id] = await api.checkConnectionChanges(c.id);
        } catch {
          // Notion blip — leave prior state; don't block the page.
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
    api.listJobs().then(setJobs);
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

  // Poll while a job is active. pollToken restarts the loop after "Update policies".
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
  }, [me?.user_id, pollToken]);

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
    setMessage("Updating changed policies… Keep this page open — we’ll confirm when it’s done.");
    try {
      await api.triggerIngest(connectionId);
      const list = await api.listJobs();
      setJobs(list);
      setPollToken((n) => n + 1);
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
            We only fetch Notion page timestamps until something actually changed —
            then you can update just those pages.
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
              />
            );
          })}
        </div>
      </main>
    </AppShell>
  );
}
