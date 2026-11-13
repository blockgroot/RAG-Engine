"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { SpaceMark, spaceStyle } from "@/components/SpaceMark";
import { api, WorkspaceMemberRecord } from "@/lib/api";

/**
 * Space details, opened from the Ask header: the people, and for owners a
 * link to `/workspaces/[id]` (not a second copy of the management page).
 */
export function SpacePanel({
  workspaceId,
  spaceName,
  isOwner,
  currentUserEmail,
  onClose,
}: {
  workspaceId: string;
  spaceName: string | null;
  isOwner: boolean;
  currentUserEmail?: string | null;
  onClose: () => void;
}) {
  const [members, setMembers] = useState<WorkspaceMemberRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    api
      .listWorkspaceMembers(workspaceId)
      .then(setMembers)
      .catch(() => setError("Could not load the people in this space."));
  }, [workspaceId]);

  useEffect(load, [load]);

  // Escape closes, and focus moves into the panel so a keyboard user is not
  // left behind on the page underneath.
  useEffect(() => {
    panelRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    const email = inviteEmail.trim().toLowerCase();
    if (!email || busy) return;
    setBusy("invite");
    setError(null);
    setNotice(null);
    try {
      await api.inviteWorkspaceMember(workspaceId, email);
      setInviteEmail("");
      setNotice(`${email} was added.`);
      load();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not add that person to this space."
      );
    } finally {
      setBusy(null);
    }
  }

  async function promote(member: WorkspaceMemberRecord) {
    if (busy) return;
    setBusy(member.user_id);
    setError(null);
    setNotice(null);
    try {
      await api.makeWorkspaceOwner(workspaceId, member.user_id);
      setNotice(`${member.email} is now an owner.`);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not make them an owner.");
    } finally {
      setBusy(null);
    }
  }

  async function remove(member: WorkspaceMemberRecord) {
    if (busy) return;
    // Removing someone is not reversible from here — they would have to be
    // invited again — so it is confirmed rather than one stray click.
    if (
      !window.confirm(
        `Remove ${member.email} from ${spaceName || "this space"}? ` +
          "They keep their company account."
      )
    ) {
      return;
    }
    setBusy(member.user_id);
    setError(null);
    setNotice(null);
    try {
      await api.removeWorkspaceMember(workspaceId, member.user_id);
      setNotice(`${member.email} was removed.`);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove them.");
    } finally {
      setBusy(null);
    }
  }

  const owners = (members || []).filter((m) => m.role === "owner").length;

  return (
    <div className="space-panel-scrim" onClick={onClose}>
      <aside
        ref={panelRef}
        tabIndex={-1}
        className="space-panel"
        style={spaceStyle(spaceName)}
        role="dialog"
        aria-modal="true"
        aria-labelledby="space-panel-title"
        // The scrim closes on click; the panel must not, or every interaction
        // inside it would dismiss the thing being interacted with.
        onClick={(e) => e.stopPropagation()}
      >
        <header className="space-panel-head">
          <SpaceMark name={spaceName} className="space-panel-mark" />
          <div>
            <p className="space-panel-eyebrow">Space</p>
            <h2 id="space-panel-title">{spaceName || "Space"}</h2>
          </div>
          <button
            type="button"
            className="space-panel-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </header>

        <div className="space-panel-body">
          {error && (
            <div className="banner banner-warn" role="alert">
              {error}
            </div>
          )}
          {notice && (
            <div className="banner banner-ok" role="status">
              {notice}
            </div>
          )}

          {members === null ? (
            <p className="muted">Loading…</p>
          ) : (
            <>
              <p className="space-panel-count">
                People · {members.length}
              </p>
              <ul className="space-panel-list">
              {members.map((m) => {
                const isSelf = Boolean(
                  currentUserEmail && m.email === currentUserEmail
                );
                // The sole owner cannot be removed (the API refuses it).
                const removable = isOwner && !(m.role === "owner" && owners <= 1);
                return (
                  <li key={m.user_id} className="space-panel-person">
                    <span className="space-panel-avatar" aria-hidden>
                      {m.email.trim().charAt(0).toUpperCase()}
                    </span>
                    <span className="space-panel-person-copy">
                      <strong title={m.email}>{m.email}</strong>
                      <span className="muted">
                        {m.role === "owner" ? "Owner" : "Member"}
                        {isSelf ? " · you" : ""}
                      </span>
                    </span>
                    {isOwner && (
                      <span className="space-panel-person-actions">
                        {m.role === "member" && (
                          <button
                            type="button"
                            className="button button-secondary button-sm"
                            disabled={busy === m.user_id}
                            onClick={() => promote(m)}
                          >
                            Make owner
                          </button>
                        )}
                        {removable && !isSelf && (
                          <button
                            type="button"
                            className="button button-secondary button-sm"
                            disabled={busy === m.user_id}
                            onClick={() => remove(m)}
                          >
                            Remove
                          </button>
                        )}
                      </span>
                    )}
                  </li>
                );
                })}
              </ul>
            </>
          )}

          {isOwner ? (
            <form className="space-panel-invite" onSubmit={invite}>
              <label htmlFor="space-panel-invite-email">Add someone</label>
              <div className="space-panel-invite-row">
                <input
                  id="space-panel-invite-email"
                  className="input"
                  type="email"
                  placeholder="colleague@company.com"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  disabled={busy === "invite"}
                />
                <button
                  type="submit"
                  className="button"
                  disabled={busy === "invite" || !inviteEmail.trim()}
                >
                  {busy === "invite" ? "…" : "Add"}
                </button>
              </div>
              <p className="muted space-panel-hint">
                They must already have a company account.
              </p>
            </form>
          ) : (
            <p className="muted space-panel-hint">
              Only an owner of this space can add or remove people.
            </p>
          )}

          {/* The owner's way through to the real management page. A quiet
              footer link, not a tab: connections, syncing and deleting all
              already live at /workspaces/[id], and duplicating any of it here
              would be a second copy free to drift. A member sees nothing —
              they have no settings to reach. */}
          {isOwner && (
            <Link href={`/workspaces/${workspaceId}`} className="space-panel-settings">
              <span>
                <strong>Connected apps &amp; space settings</strong>
                <small>Sources, Agent mode and deleting the space</small>
              </span>
              <i aria-hidden>→</i>
            </Link>
          )}
        </div>
      </aside>
    </div>
  );
}
