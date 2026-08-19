/*
 * Session-lifetime cache for ask-screen suggestion chips.
 *
 * chat/page.tsx reads it (stale-while-revalidate, same shape as useMe.ts:
 * serve cached chips instantly, then refetch in the background). Any page
 * that changes the underlying content for a scope -- admin/connections
 * (org-wide) or workspaces/[id] (one workspace) -- must invalidate it after
 * an ingest/update/disconnect completes. Without this, a chip list built
 * from yesterday's document titles or repo list can keep showing until a
 * hard refresh, even though the sync that made it stale already finished.
 */
const cache = new Map<string, string[]>();

export function suggestionsCacheKey(
  agent: "policy" | "github" | "slack" | "linear" | "notion" | "google",
  workspaceId: string | null
): string {
  return `${agent}:${workspaceId ?? "org"}`;
}

export function getCachedSuggestions(key: string): string[] | undefined {
  return cache.get(key);
}

export function setCachedSuggestions(key: string, value: string[]): void {
  cache.set(key, value);
}

/**
 * Drop cached chips for one scope (org-wide when `workspaceId` is `null`,
 * else that workspace) so the next ask-screen visit re-fetches instead of
 * showing chips built from a document/repo list that just changed.
 */
export function invalidateSuggestionsCache(workspaceId: string | null): void {
  cache.delete(suggestionsCacheKey("policy", workspaceId));
  cache.delete(suggestionsCacheKey("github", workspaceId));
}
