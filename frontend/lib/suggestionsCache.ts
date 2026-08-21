/* Session-lifetime cache for ask-screen suggestion chips. */
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

export function invalidateSuggestionsCache(workspaceId: string | null): void {
  cache.delete(suggestionsCacheKey("policy", workspaceId));
  cache.delete(suggestionsCacheKey("github", workspaceId));
  cache.delete(suggestionsCacheKey("slack", workspaceId));
  cache.delete(suggestionsCacheKey("linear", workspaceId));
  cache.delete(suggestionsCacheKey("notion", workspaceId));
  cache.delete(suggestionsCacheKey("google", workspaceId));
}
