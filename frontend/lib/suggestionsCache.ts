/* Session-lifetime cache for ask-screen suggestion chips.
 *
 * Keyed on SCOPE alone. It used to be keyed on `(agent, workspaceId)` because
 * each per-source tab had its own chip set; Ask is one box now and the chips
 * span every connected source, so there is one entry per scope and
 * invalidation is a single delete rather than a list of six that had to be
 * kept in step with the providers. */
export type CachedSuggestions = {
  questions: string[];
  /** Deep mode's starters: whole-document questions. */
  deep: string[];
  /** Which connector each question came from, for its logo. */
  sources: Record<string, string>;
};

const cache = new Map<string, CachedSuggestions>();

export function suggestionsCacheKey(workspaceId: string | null): string {
  return workspaceId ?? "org";
}

export function getCachedSuggestions(key: string): CachedSuggestions | undefined {
  return cache.get(key);
}

export function setCachedSuggestions(key: string, value: CachedSuggestions): void {
  cache.set(key, value);
}

export function invalidateSuggestionsCache(workspaceId: string | null): void {
  cache.delete(suggestionsCacheKey(workspaceId));
}
