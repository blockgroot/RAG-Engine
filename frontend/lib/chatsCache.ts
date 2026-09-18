/* Module-lifetime cache for the rail's Recent Chats list.
 *
 * `AppShell` is rendered by each PAGE rather than by a shared layout, so every
 * navigation remounts `RailChats`: its `loaded` flag reset to false, the
 * component returned null, and the whole section disappeared and re-fetched on
 * each click. A rail that flickers away while you use the app reads as the app
 * reloading itself.
 *
 * Seeding initial state from here makes a remount render the previous list
 * immediately while the refetch happens behind it — the list is a few rows of
 * titles, so showing a second-old copy for 200ms costs nothing, where an empty
 * gap costs the illusion that the rail is part of the page rather than part of
 * the route.
 *
 * Module scope, not sessionStorage: this is a render-smoothing cache, not
 * state worth persisting, and it should die with the tab's JS context. Same
 * shape as `suggestionsCache`, keyed on scope for the same reason.
 */
import type { ConversationSummary } from "./api";

const cache = new Map<string, ConversationSummary[]>();

export function chatsCacheKey(workspaceId: string | null): string {
  return workspaceId ?? "org";
}

export function getCachedChats(key: string): ConversationSummary[] | undefined {
  return cache.get(key);
}

export function setCachedChats(key: string, value: ConversationSummary[]): void {
  cache.set(key, value);
}
