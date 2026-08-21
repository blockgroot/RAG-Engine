/** Shared ingest-job polling. */

"use client";

import { useEffect, useRef } from "react";
import { api, JobRecord } from "./api";

export const JOB_POLL_MS = 8000;
export const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);

export function useJobPolling(options: {
  enabled: boolean;
  jobId?: string | null;
  workspaceId?: string | null;
  pollToken?: number;
  intervalMs?: number;
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
