"use client";

import { useEffect, useState } from "react";
import { api, ConnectionSourceConfig, SlackChannel } from "@/lib/api";

/**
 * Channel picker for a Slack connection (org-wide or workspace-scoped).
 *
 * Unlike Drive's folder picker, Slack's own OAuth grant screen doesn't let
 * the installer pick channels, so this is a REQUIRED second step after
 * connect, not an optional refinement — see
 * docs/plans/2026-08-17-slack-integration.md §5. Channels the bot can't yet
 * see (private, not invited) are shown but disabled with an explanation
 * (decision D7: no auto-join API exists for private channels).
 */
export function SlackChannelPicker({
  connectionId,
  workspaceId,
  currentChannelIds,
  onSaved,
  onError,
  onCancel,
}: {
  connectionId: string;
  workspaceId?: string;
  currentChannelIds?: string[];
  onSaved: (
    config: ConnectionSourceConfig,
    meta?: { channels_changed?: boolean; documents_purged?: number }
  ) => void;
  onError?: (message: string) => void;
  onCancel?: () => void;
}) {
  const [channels, setChannels] = useState<SlackChannel[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set(currentChannelIds || []));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    const list = workspaceId
      ? api.listWorkspaceConnectionSlackChannels(workspaceId, connectionId)
      : api.listConnectionSlackChannels(connectionId);
    list
      .then(({ channels }) => {
        if (cancelled) return;
        setChannels(channels);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "Could not list Slack channels.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
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
      const result = workspaceId
        ? await api.setWorkspaceConnectionSlackChannels(workspaceId, connectionId, Array.from(selected))
        : await api.setConnectionSlackChannels(connectionId, Array.from(selected));
      onSaved(result.config, {
        channels_changed: result.channels_changed,
        documents_purged: result.documents_purged,
      });
    } catch (err) {
      onError?.(err instanceof Error ? err.message : "Could not save channels.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <p className="muted">Loading channels…</p>;
  }
  if (loadError) {
    return <div className="banner banner-warn">{loadError}</div>;
  }

  const list = channels || [];

  return (
    <div className="stack">
      <p className="muted" style={{ margin: 0 }}>
        Pick which channels Ask can answer questions from. A channel marked
        "Invite the bot" needs a human to run <code>/invite</code> in Slack
        first — there is no way to grant private-channel access automatically.
      </p>
      {list.length === 0 && (
        <p className="muted">No channels visible yet — invite the bot to a channel in Slack, then reopen this picker.</p>
      )}
      <div className="stack" style={{ gap: "0.4rem", maxHeight: "260px", overflowY: "auto" }}>
        {list.map((ch) => {
          const disabled = ch.is_private && !ch.is_member;
          return (
            <label
              key={ch.id}
              className="checkbox-row"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                opacity: disabled ? 0.55 : 1,
              }}
              title={disabled ? "Invite the bot to this private channel in Slack first" : undefined}
            >
              <input
                type="checkbox"
                checked={selected.has(ch.id)}
                disabled={disabled}
                onChange={() => toggle(ch.id)}
              />
              <span>
                {ch.is_private ? "🔒 " : "# "}
                {ch.name}
              </span>
              {disabled && <span className="badge">Invite the bot</span>}
            </label>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <button className="button" type="button" disabled={saving || selected.size === 0} onClick={save}>
          {saving ? "Saving…" : "Save channels"}
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
