"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, WorkspaceMemberRecord } from "@/lib/api";

type Tab = "members" | "about";

/**
 * Space details, opened from the Ask header — the Slack pattern.
 *
 * Everyone (owner included) now lands on Ask when they open a space, because
 * asking is what a space is FOR. This panel is where the people and the
 * settings live, one click away, exactly as a Slack channel's details sit
 * behind its name rather than in front of the conversation.
 *
 * Tabbed rather than one long column: membership and configuration are
 * different jobs, and stacking every owner control into a single scroll is the
 * clutter this replaces. Owner-only actions stay INSIDE the tab they belong to
 * — promoting someone is a membership action, deleting the space is not.
 *
 * Deliberately does NOT re-implement the management page. Connections, sync and
 * delete already work at `/workspaces/[id]`; the About tab links there for
 * owners instead of growing a second copy that can drift.
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
  const [tab, setTab] = useState<Tab>("members");
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
        role="dialog"
        aria-modal="true"
        aria-labelledby="space-panel-title"
        // The scrim closes on click; the panel must not, or every interaction
        // inside it would dismiss the thing being interacted with.
        onClick={(e) => e.stopPropagation()}
      >
        <header className="space-panel-head">
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

        <div className="space-panel-tabs" role="tablist" aria-label="Space details">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "members"}
            className={`space-panel-tab${tab === "members" ? " is-active" : ""}`}
            onClick={() => setTab("members")}
          >
            People{members ? ` (${members.length})` : ""}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "about"}
            className={`space-panel-tab${tab === "about" ? " is-active" : ""}`}
            onClick={() => setTab("about")}
          >
            About
          </button>
        </div>

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

          {tab === "members" ? (
            <>
              {members === null ? (
                <p className="muted">Loading…</p>
              ) : (
                <ul className="space-panel-list">
                  {members.map((m) => {
                    const isSelf = Boolean(
                      currentUserEmail && m.email === currentUserEmail
                    );
                    // The sole owner cannot be removed — the API refuses it, and
                    // offering a button that always errors is worse than not
                    // offering one.
                    const removable =
                      isOwner && !(m.role === "owner" && owners <= 1);
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
            </>
          ) : (
            <div className="space-panel-about">
              <p>
                Answers in this space are drawn only from the sources connected
                to it — never from company-wide documents.
              </p>
              {isOwner ? (
                <>
                  <p className="muted">
                    Connected apps, syncing and deleting this space are managed
                    on its settings page.
                  </p>
                  <Link
                    href={`/workspaces/${workspaceId}`}
                    className="button button-secondary"
                  >
                    Space settings
                  </Link>
                </>
              ) : (
                <p className="muted">
                  An owner of this space manages which apps it can read.
                </p>
              )}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
