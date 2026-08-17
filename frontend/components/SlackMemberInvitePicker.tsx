"use client";

import { useEffect, useState } from "react";
import { api, SlackChannelMember } from "@/lib/api";

/**
 * After a workspace owner connects Slack channels, let them invite members of
 * those channels into this workspace — without leaving Handbook to look up
 * emails by hand. Backend-only until now (the endpoints existed, nothing
 * called them): see docs/plans/2026-08-17-slack-integration.md.
 *
 * Only members who already have a Handbook account in this org are
 * selectable (``already_org_member``) — the backend refuses anyone else
 * (workspaces never create new org accounts from this flow, see
 * ``workspaces.invite_member``), so this UI reflects that rather than
 * fighting it: non-org members are shown, not hidden, but disabled with an
 * explanation.
 */
export function SlackMemberInvitePicker({
  workspaceId,
  connectionId,
  channelIds,
  channelNames,
  onInvited,
}: {
  workspaceId: string;
  connectionId: string;
  channelIds: string[];
  channelNames?: Record<string, string>;
  /** Fires after at least one invite actually succeeded — refresh "People in this space" here. */
  onInvited?: () => void;
}) {
  type Row = SlackChannelMember & { channelId: string };

  const [rows, setRows] = useState<Row[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{ invited: string[]; skipped: string[] } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    Promise.all(
      channelIds.map((channelId) =>
        api
          .listWorkspaceSlackChannelMembers(workspaceId, connectionId, channelId)
          .then(({ members }) => members.map((m) => ({ ...m, channelId })))
      )
    )
      .then((lists) => {
        if (cancelled) return;
        // One row per email — a member in two connected channels is only
        // asked about once, attributed to whichever channel listed them first.
        const seen = new Set<string>();
        const merged: Row[] = [];
        for (const row of lists.flat()) {
          if (seen.has(row.email)) continue;
          seen.add(row.email);
          merged.push(row);
        }
        setRows(merged);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "Could not list channel members.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, connectionId, channelIds.join(",")]);

  function toggle(email: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(email)) next.delete(email);
      else next.add(email);
      return next;
    });
  }

  async function sendInvites() {
    if (!rows || selected.size === 0) return;
    setSending(true);
    setLoadError(null);
    try {
      // Group selected emails by the channel they came from — the endpoint
      // is per-channel, though membership it grants is workspace-wide.
      const byChannel = new Map<string, string[]>();
      for (const row of rows) {
        if (!selected.has(row.email)) continue;
        const list = byChannel.get(row.channelId) || [];
        list.push(row.email);
        byChannel.set(row.channelId, list);
      }
      const invited: string[] = [];
      const skipped: string[] = [];
      for (const [channelId, emails] of byChannel) {
        const res = await api.inviteWorkspaceSlackChannelMembers(
          workspaceId,
          connectionId,
          channelId,
          emails
        );
        invited.push(...res.invited);
        skipped.push(...res.skipped_not_org_member);
      }
      setResult({ invited, skipped });
      setSelected(new Set());
      if (invited.length > 0) {
        const invitedSet = new Set(invited);
        setRows((prev) =>
          (prev || []).map((row) =>
            invitedSet.has(row.email) ? { ...row, already_workspace_member: true } : row
          )
        );
        onInvited?.();
      }
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Could not send invites.");
    } finally {
      setSending(false);
    }
  }

  if (loading) return <p className="muted">Loading channel members…</p>;
  if (loadError) return <div className="banner banner-warn">{loadError}</div>;

  const list = rows || [];
  const eligible = list.filter((m) => m.already_org_member && !m.already_workspace_member);

  return (
    <div className="stack" style={{ gap: "0.6rem" }}>
      <p className="muted" style={{ margin: 0 }}>
        People from {channelIds.length === 1 ? "this channel" : "these channels"} who already
        have a Handbook account can be added to this space directly. Anyone not on Handbook yet
        needs an admin to invite them to the org first.
      </p>
      {list.length === 0 && <p className="muted">No members found for the connected channel(s).</p>}
      {list.length > 0 && eligible.length === 0 && (
        <p className="muted">No connected channel member has a Handbook account yet.</p>
      )}
      <div className="stack" style={{ gap: "0.4rem", maxHeight: "260px", overflowY: "auto" }}>
        {list.map((m) => {
          const disabled = !m.already_org_member || m.already_workspace_member;
          return (
            <label
              key={m.email}
              className="checkbox-row"
              style={{ display: "flex", alignItems: "center", gap: "0.5rem", opacity: disabled ? 0.55 : 1 }}
              title={
                m.already_workspace_member
                  ? "Already in this space"
                  : !m.already_org_member
                    ? "Not on Handbook yet — an admin needs to invite them to the org first"
                    : undefined
              }
            >
              <input
                type="checkbox"
                checked={selected.has(m.email)}
                disabled={disabled}
                onChange={() => toggle(m.email)}
              />
              <span>
                {m.name} <span className="muted">({m.email})</span>
                {channelNames?.[m.channelId] ? ` · #${channelNames[m.channelId]}` : ""}
              </span>
              {m.already_workspace_member && <span className="badge badge-verified">In space</span>}
              {!m.already_org_member && <span className="badge">Not on Handbook yet</span>}
            </label>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        <button
          className="button"
          type="button"
          disabled={sending || selected.size === 0}
          onClick={sendInvites}
        >
          {sending ? "Sending…" : `Invite ${selected.size || ""}`.trim()}
        </button>
      </div>
      {result && (
        <p className="muted" style={{ margin: 0 }}>
          {result.invited.length > 0 ? `Invited ${result.invited.length}.` : ""}
          {result.skipped.length > 0
            ? ` ${result.skipped.length} skipped (not on Handbook yet).`
            : ""}
        </p>
      )}
    </div>
  );
}
