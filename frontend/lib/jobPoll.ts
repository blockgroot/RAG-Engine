/** Shared ingest-job polling — longer interval, single-job when possible, pause when hidden. */

"use client";

import { useEffect, useRef } from "react";
import { api, JobRecord } from "./api";

export const JOB_POLL_MS = 8000;
export const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);

/**
 * Poll ingestion job status until ``shouldContinue`` returns false.
 *
 * - Prefers a single-job GET when ``jobId`` is set (one cheap request).
 * - Falls back to the jobs list when discovering / watching multiple jobs.
 * - With ``workspaceId``, uses workspace-scoped job endpoints; otherwise admin.
 * - Does **not** call readiness endpoints — callers refresh those on terminal status.
 * - Skips network work while the document tab is hidden; resumes on focus.
 */
export function useJobPolling(options: {
  enabled: boolean;
  /** Prefer this job id; omit/null to list all jobs each tick. */
  jobId?: string | null;
  /** When set, poll ``/workspaces/{id}/jobs…`` instead of ``/admin/jobs…``. */
  workspaceId?: string | null;
  /** Bump to restart the loop (e.g. after triggering ingest). */
  pollToken?: number;
  intervalMs?: number;
  /** Return true to keep polling. */
  onJobs: (jobs: JobRecord[]) => boolean;
}): void {
  const {
    enabled,
    jobId,
    workspaceId = null,
    pollToken = 0,
    intervalMs = JOB_POLL_MS,
    onJobs,
  } = options;
  const onJobsRef = useRef(onJobs);
  onJobsRef.current = onJobs;
  const jobIdRef = useRef(jobId);
  jobIdRef.current = jobId;
  const workspaceIdRef = useRef(workspaceId);
  workspaceIdRef.current = workspaceId;

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    function schedule(ms: number = intervalMs) {
      if (cancelled) return;
      timer = setTimeout(() => {
        void tick();
      }, ms);
    }

    async function fetchJobs(): Promise<JobRecord[] | null> {
      const id = jobIdRef.current;
      const ws = workspaceIdRef.current;
      if (id) {
        const job = ws
          ? await api.getWorkspaceJob(ws, id).catch(() => null)
          : await api.getJob(id).catch(() => null);
        return job ? [job] : null;
      }
      return ws
        ? api.listWorkspaceJobs(ws).catch(() => null)
        : api.listJobs().catch(() => null);
    }

    async function tick() {
      if (cancelled) return;
      if (typeof document !== "undefined" && document.hidden) {
        schedule(intervalMs);
        return;
      }
      try {
        const jobs = await fetchJobs();
        if (cancelled || !jobs) {
          schedule(intervalMs * 2);
          return;
        }
        const keep = onJobsRef.current(jobs);
        if (!cancelled && keep) {
          schedule(intervalMs);
        }
      } catch {
        if (!cancelled) schedule(intervalMs * 2);
      }
    }

    function onVisibility() {
      if (cancelled || document.hidden) return;
      if (timer) clearTimeout(timer);
      void tick();
    }

    void tick();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled, pollToken, intervalMs, jobId, workspaceId]);
}
