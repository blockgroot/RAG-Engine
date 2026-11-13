/* Module-lifetime cache for the rail's Recent Chats, keyed on scope. Every
 * page remounts `RailChats`; seeding from here stops the list flickering away.
 * Not persisted: it dies with the tab. */
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
