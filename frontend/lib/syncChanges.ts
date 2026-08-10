import type { SyncChanges } from "./api";

/** Clear the Update affordance after a successful sync (before re-check finishes). */
export function clearedSyncChanges(connectionId: string): SyncChanges {
  return {
    connection_id: connectionId,
    new_count: 0,
    updated_count: 0,
    removed_count: 0,
    unchanged_count: 0,
    remote_total: 0,
    has_changes: false,
  };
}
