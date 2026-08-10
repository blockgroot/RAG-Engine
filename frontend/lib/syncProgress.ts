/** Shared copy for live ingestion progress (onboarding + Sources / Spaces). */

import type { JobRecord } from "./api";

export function syncPhaseHeadline(job: JobRecord | undefined): string {
  const phase = job?.phase;
  if (phase === "listing") return "Looking at what's there…";
  if (phase === "preparing") return "Opening the next page…";
  if (phase === "contextualizing") return "Adding search context…";
  if (phase === "embedding") return "Indexing for search…";
  if (phase === "enriching") return "Improving search quality…";
  return "Updating…";
}

/** Notion-style "N of M pages" line while a job is queued/running. */
export function syncPagesDetail(job: JobRecord | undefined): string {
  const total = job?.total_documents ?? null;
  const done = job?.processed_documents ?? 0;
  if (job?.phase === "listing") {
    return "Checking for pages that are new or changed.";
  }
  if (total != null && total > 0) {
    const current = Math.min(total, done + 1);
    if (done < total) {
      return `${done} of ${total} pages done · working on page ${current}`;
    }
    return `${done} of ${total} pages done.`;
  }
  if (total === 0) return "Nothing new to bring in — finishing up.";
  if (job?.status === "queued") return "Waiting for the sync worker…";
  return "This can take a few minutes for large folders.";
}

export function syncPercent(job: JobRecord | undefined): number | null {
  const total = job?.total_documents ?? null;
  if (total == null || total <= 0) return null;
  const done = job?.processed_documents ?? 0;
  return Math.min(100, Math.round((done / total) * 100));
}
