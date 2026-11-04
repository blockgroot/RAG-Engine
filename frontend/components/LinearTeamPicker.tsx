"use client";

import { useEffect, useState } from "react";
import { api, ConnectionSourceConfig, LinearTeam } from "@/lib/api";

/** Which Linear teams a connection reads. Linear's own sign-in grants every
 *  team the account can see, so the choice is made here; nothing is indexed
 *  until at least one team is picked. */
export function LinearTeamPicker({
  connectionId,
  workspaceId,
  currentTeamIds,
  onSaved,
  onError,
  onCancel,
}: {
  connectionId: string;
  workspaceId?: string;
  currentTeamIds?: string[];
  onSaved: (
    config: ConnectionSourceConfig,
    meta?: { teams_changed?: boolean; documents_purged?: number }
  ) => void;
  onError?: (message: string) => void;
  onCancel?: () => void;
}) {
  const [teams, setTeams] = useState<LinearTeam[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set(currentTeamIds || []));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    api
      .listLinearTeams(connectionId, workspaceId)
      .then(({ teams }) => !cancelled && setTeams(teams))
      .catch((err) => !cancelled && setLoadError(err instanceof Error ? err.message : "Could not list Linear teams."));
    return () => {
      cancelled = true;
    };
  }, [connectionId, workspaceId]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function save() {
    if (selected.size === 0) return;
    setSaving(true);
    onError?.("");
    try {
      const result = await api.setLinearTeams(connectionId, Array.from(selected), workspaceId);
      onSaved(result.config, {
        teams_changed: result.teams_changed,
        documents_purged: result.documents_purged,
      });
    } catch (err) {
      onError?.(err instanceof Error ? err.message : "Could not save teams.");
    } finally {
      setSaving(false);
    }
  }

  if (loadError) return <div className="banner banner-warn">{loadError}</div>;
  if (!teams) return <p className="muted">Loading teams…</p>;

  return (
    <div className="stack">
      <p className="muted" style={{ margin: 0 }}>
        Pick which teams&rsquo; issues Ask can answer from. A private team&rsquo;s issues are still
        shown only to its members.
      </p>
      {teams.length === 0 && <p className="muted">This Linear account can&rsquo;t see any teams.</p>}
      <div className="stack" style={{ gap: "0.4rem", maxHeight: "260px", overflowY: "auto" }}>
        {teams.map((t) => (
          <label key={t.id} className="checkbox-row" style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <input type="checkbox" checked={selected.has(t.id)} onChange={() => toggle(t.id)} />
            <span>
              {t.private ? "🔒 " : ""}
              {t.name}
            </span>
            {t.key && <span className="muted">{t.key}</span>}
          </label>
        ))}
      </div>
      <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <button className="button" type="button" disabled={saving || selected.size === 0} onClick={save}>
          {saving ? "Saving…" : "Save teams"}
        </button>
        {onCancel && (
          <button className="button button-secondary" type="button" disabled={saving} onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}
