"use client";

import { useEffect, useRef, useState } from "react";
import { api, ConnectionSourceConfig, DriveFolder } from "@/lib/api";

const FOLDER_SEARCH_DEBOUNCE_MS = 450;

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
