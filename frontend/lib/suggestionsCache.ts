/* Session-lifetime cache for Ask's suggestion chips, keyed on scope alone:
 * the chips span every connected source. */
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
