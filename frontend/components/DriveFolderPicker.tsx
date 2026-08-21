"use client";

import { useEffect, useRef, useState } from "react";
import { api, ConnectionSourceConfig, DriveFolder } from "@/lib/api";

// Raised from 300ms. Each search is expensive on the server side — four
// sequential DB round trips before the Google call even starts — so a keystroke
// that will immediately be superseded is pure waste. 450ms still feels
// responsive while typing but issues far fewer doomed requests.
const FOLDER_SEARCH_DEBOUNCE_MS = 450;

/**
 * Search-as-you-type Drive folder picker (org-wide or workspace-scoped).
 *
 * Selecting a result saves by folder id; pasting a Drive URL/id and clicking
 * Save still works as a fallback — same UX as Sources / personal workspaces.
 *
 * ``mode="change"`` is for swapping an already-saved folder: same API, clearer
 * copy, and Cancel to dismiss without saving.
 */
export function DriveFolderPicker({
  connectionId,
  workspaceId,
  inputId = "drive-folder",
  mode = "set",
  currentFolderId,
  currentFolderName,
  onSaved,
  onError,
  onCancel,
}: {
  connectionId: string;
  workspaceId?: string;
  inputId?: string;
  mode?: "set" | "change";
  /** When set, saving the same id is a no-op (closes via onCancel if provided). */
  currentFolderId?: string | null;
  currentFolderName?: string | null;
  onSaved: (
    config: ConnectionSourceConfig,
    meta?: { folder_changed?: boolean; documents_purged?: number }
  ) => void;
  onError?: (message: string) => void;
  onCancel?: () => void;
}) {
  const [folderUrl, setFolderUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [folderResults, setFolderResults] = useState<DriveFolder[]>([]);
  const [searchingFolders, setSearchingFolders] = useState(false);
  const [folderSearchError, setFolderSearchError] = useState<string | null>(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);

  /*
   * Results already fetched, keyed by the exact query string.
   *
   * Every search costs four sequential DB round trips plus a Google call, and
   * the picker previously re-paid that for queries it had already answered:
   * closing and reopening the dropdown re-ran the *empty* query — the most
   * expensive one, since it asks Drive for every folder across every shared
   * drive ordered by modified time — and backspacing re-ran a prefix that had
   * just been fetched. A cache hit now renders instantly with no request and no
   * spinner at all.
   *
   * Scoped to this mounted picker (a ref, not a module global) and keyed by
   * query alone, which is safe precisely because the component is already bound
   * to one connectionId/workspaceId — a different connection is a different
   * mount with a different cache, so results can never cross connections.
   * Deliberately not persisted: a folder created in Drive moments ago should
   * appear on the next fresh open, so the cache lives only as long as the
   * picker is on screen.
   */
  const folderCache = useRef<Map<string, DriveFolder[]>>(new Map());

  useEffect(() => {
    if (!dropdownOpen) return;
    const query = folderUrl.trim();

    const cached = folderCache.current.get(query);
    if (cached) {
      setFolderResults(cached);
      setFolderSearchError(null);
      setSearchingFolders(false);
      return;
    }

    let cancelled = false;
    setSearchingFolders(true);
    const timer = setTimeout(async () => {
      try {
        const { folders } = workspaceId
          ? await api.searchWorkspaceConnectionDriveFolders(
              workspaceId,
              connectionId,
              query
            )
          : await api.searchConnectionDriveFolders(connectionId, query);
        if (cancelled) return;
        folderCache.current.set(query, folders);
        setFolderResults(folders);
        setFolderSearchError(null);
      } catch (err) {
        if (cancelled) return;
        // Failures are NOT cached — a timeout or a transient Drive error must be
        // retryable, not remembered as "this query has no folders".
        setFolderResults([]);
        setFolderSearchError(
          err instanceof Error ? err.message : "Could not search Drive folders."
        );
      } finally {
        if (!cancelled) setSearchingFolders(false);
      }
    }, FOLDER_SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [folderUrl, dropdownOpen, connectionId, workspaceId]);

  async function saveFolder(value: string) {
    if (!value) return;
    // Same folder already linked (bare id or URL containing it) — dismiss.
    if (
      currentFolderId &&
      (value === currentFolderId || value.includes(currentFolderId))
    ) {
      onCancel?.();
      return;
    }
    setSaving(true);
    onError?.("");
    try {
      const result = workspaceId
        ? await api.setWorkspaceConnectionConfig(workspaceId, connectionId, value)
        : await api.setConnectionConfig(connectionId, value);
      setFolderUrl("");
      setFolderResults([]);
      setDropdownOpen(false);
      onSaved(result.config, {
        folder_changed: result.folder_changed,
        documents_purged: result.documents_purged,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Could not save folder.";
      onError?.(message);
    } finally {
      setSaving(false);
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    saveFolder(folderUrl.trim());
  }

  const isChange = mode === "change";

  return (
    <form onSubmit={handleSubmit} className="stack">
      <p className="muted" style={{ margin: 0 }}>
        {isChange
          ? currentFolderName
            ? `Currently: ${currentFolderName}. Pick a different folder — Update will drop docs that are no longer in scope.`
            : "Pick a different Drive folder. Update will drop docs that are no longer in scope."
          : "Choose a Drive folder to use."}
      </p>
      <div className="field folder-picker-field">
        <label htmlFor={inputId}>{isChange ? "New folder" : "Folder"}</label>
        <input
          id={inputId}
          className="input"
          type="text"
          required
          autoComplete="off"
          placeholder="Search folders or paste a link…"
          value={folderUrl}
          onChange={(e) => setFolderUrl(e.target.value)}
          onFocus={() => setDropdownOpen(true)}
          onBlur={() => setTimeout(() => setDropdownOpen(false), 150)}
        />
        {dropdownOpen && (searchingFolders || folderResults.length > 0 || folderSearchError) && (
          <div className="folder-dropdown" role="listbox">
            {searchingFolders && <div className="folder-dropdown-status">Searching…</div>}
            {!searchingFolders && folderSearchError && (
              <div className="folder-dropdown-status">{folderSearchError}</div>
            )}
            {!searchingFolders && !folderSearchError && folderResults.length === 0 && (
              <div className="folder-dropdown-status">
                No matches — paste a folder link instead.
              </div>
            )}
            {!searchingFolders &&
              folderResults.map((folder) => (
                <button
                  key={folder.id}
                  type="button"
                  className="folder-dropdown-item"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => saveFolder(folder.id)}
                >
                  {folder.name}
                  {currentFolderId && folder.id === currentFolderId ? " (current)" : ""}
                </button>
              ))}
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <button className="button" type="submit" disabled={saving || !folderUrl.trim()}>
          {saving ? "Saving…" : isChange ? "Save new folder" : "Save folder"}
        </button>
        {isChange && onCancel && (
          <button
            className="button button-secondary"
            type="button"
            disabled={saving}
            onClick={onCancel}
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
