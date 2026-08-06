"use client";

import { useEffect, useState } from "react";
import { api, ConnectionSourceConfig, DriveFolder } from "@/lib/api";

const FOLDER_SEARCH_DEBOUNCE_MS = 300;

/**
 * Search-as-you-type Drive folder picker (org-wide or workspace-scoped).
 *
 * Selecting a result saves by folder id; pasting a Drive URL/id and clicking
 * Save still works as a fallback — same UX as Sources / personal workspaces.
 */
export function DriveFolderPicker({
  connectionId,
  workspaceId,
  inputId = "drive-folder",
  onSaved,
  onError,
}: {
  connectionId: string;
  workspaceId?: string;
  inputId?: string;
  onSaved: (config: ConnectionSourceConfig) => void;
  onError?: (message: string) => void;
}) {
  const [folderUrl, setFolderUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [folderResults, setFolderResults] = useState<DriveFolder[]>([]);
  const [searchingFolders, setSearchingFolders] = useState(false);
  const [folderSearchError, setFolderSearchError] = useState<string | null>(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);

  useEffect(() => {
    if (!dropdownOpen) return;
    let cancelled = false;
    setSearchingFolders(true);
    const timer = setTimeout(async () => {
      try {
        const { folders } = workspaceId
          ? await api.searchWorkspaceConnectionDriveFolders(
              workspaceId,
              connectionId,
              folderUrl.trim()
            )
          : await api.searchConnectionDriveFolders(connectionId, folderUrl.trim());
        if (cancelled) return;
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
    setSaving(true);
    onError?.("");
    try {
      const result = workspaceId
        ? await api.setWorkspaceConnectionConfig(workspaceId, connectionId, value)
        : await api.setConnectionConfig(connectionId, value);
      setFolderUrl("");
      setFolderResults([]);
      setDropdownOpen(false);
      onSaved(result.config);
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

  return (
    <form onSubmit={handleSubmit} className="stack">
      <p className="muted" style={{ margin: 0 }}>
        Choose a Drive folder to use.
      </p>
      <div className="field" style={{ position: "relative" }}>
        <label htmlFor={inputId}>Folder</label>
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
                </button>
              ))}
          </div>
        )}
      </div>
      <button className="button" type="submit" disabled={saving || !folderUrl.trim()}>
        {saving ? "Saving…" : "Save folder"}
      </button>
    </form>
  );
}
