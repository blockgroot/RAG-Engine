"use client";

import { useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { api, MemberRecord } from "@/lib/api";

/** Ten distinct colours, given out in joining order: the first ten people never share one. */
const FACE_HUES = [168, 262, 24, 340, 204, 142, 290, 48, 0, 226];

/** "sana.asiwal@x.com" → "Sana Asiwal": a readable name until people set one here. */
function nameFrom(email: string): string {
  return (email.split("@")[0] || email)
    .split(/[._-]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export default function MembersPage() {
  const { me, loading } = useMe({ requireAdmin: true });
  const [members, setMembers] = useState<MemberRecord[]>([]);
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteMessage, setInviteMessage] = useState<string | null>(null);
  const [rosterError, setRosterError] = useState<string | null>(null);
  const [rosterMessage, setRosterMessage] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const clearRosterTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function refresh() {
    api.listMembers().then(setMembers);
  }

  function flashRoster(ok: string | null, err: string | null = null) {
    if (clearRosterTimer.current) clearTimeout(clearRosterTimer.current);
    setRosterError(err);
    setRosterMessage(ok);
    if (ok) {
      clearRosterTimer.current = setTimeout(() => setRosterMessage(null), 4000);
    }
  }

  useEffect(() => {
    if (me) refresh();
    return () => {
      if (clearRosterTimer.current) clearTimeout(clearRosterTimer.current);
    };
  }, [me]);

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    setInviteError(null);
    setInviteMessage(null);
    setSending(true);
    try {
      const invited = await api.inviteMember(email.trim().toLowerCase());
      const who = email.trim().toLowerCase();
      setInviteMessage(
        invited.dev_link
          ? `Invite sent to ${who}. Dev link: ${invited.dev_link}`
          : `Invite sent to ${who}.`
      );
      setEmail("");
      refresh();
    } catch (err) {
      setInviteError(err instanceof Error ? err.message : "Could not invite that email.");
    } finally {
      setSending(false);
    }
  }

  async function handlePromote(userId: string, memberEmail: string) {
    if (busyId) return;
    if (!window.confirm(`Make ${memberEmail} an admin? They will be able to manage people and sources.`)) {
      return;
    }
    setBusyId(userId);
    flashRoster(null);
    try {
      await api.promoteMember(userId);
      flashRoster(`${memberEmail} is now an admin.`);
      refresh();
    } catch (err) {
      flashRoster(null, err instanceof Error ? err.message : "Could not promote that person.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDemote(userId: string, memberEmail: string) {
    if (busyId) return;
    const admins = members.filter((m) => m.role === "admin").length;
    if (admins <= 1) {
      flashRoster(null, "Cannot demote the last admin. Promote someone else first.");
      return;
    }
    if (!window.confirm(`Demote ${memberEmail} to member? They will lose admin access immediately.`)) {
      return;
    }
    setBusyId(userId);
    flashRoster(null);
    try {
      await api.demoteMember(userId);
      flashRoster(`Demoted ${memberEmail} — they are a member again.`);
      refresh();
    } catch (err) {
      flashRoster(null, err instanceof Error ? err.message : "Could not demote that person.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleRemove(userId: string, memberEmail: string) {
    if (busyId) return;
    if (userId === me?.user_id) {
      flashRoster(null, "You cannot remove your own account.");
      return;
    }
    if (!window.confirm(`Remove ${memberEmail} from this company? They will lose access immediately.`)) {
      return;
    }
    setBusyId(userId);
    flashRoster(null);
    try {
      await api.removeMember(userId);
      flashRoster(`Removed ${memberEmail}.`);
      refresh();
    } catch (err) {
      flashRoster(null, err instanceof Error ? err.message : "Could not remove that person.");
    } finally {
      setBusyId(null);
    }
  }

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  const adminCount = members.filter((m) => m.role === "admin").length;
  const joinOrder = new Map(
    [...members].sort((x, y) => x.created_at.localeCompare(y.created_at)).map((m, i) => [m.id, i]),
  );
  const faceStyle = (id: string) => ({ ["--face-h" as string]: FACE_HUES[(joinOrder.get(id) ?? 0) % FACE_HUES.length] });
  const memberCount = members.length - adminCount;

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Company"
          title="People"
          description="Invite teammates, make admins for succession, and remove people who leave. Always keep at least one other admin before you leave."
        />

        <section className="people-bar" aria-labelledby="invite-title">
          <dl className="people-stats">
            <div><dt>People</dt><dd>{members.length}</dd></div>
            <div><dt>Admins</dt><dd>{adminCount}</dd></div>
            <div><dt>Members</dt><dd>{memberCount}</dd></div>
          </dl>
          <form onSubmit={handleInvite} className="people-invite-form">
            <label id="invite-title" htmlFor="email">Invite someone</label>
            <div>
              <input
                id="email"
                className="input"
                type="email"
                placeholder="teammate@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                disabled={sending}
              />
              <button className="button" type="submit" disabled={sending || !email.trim()}>
                {sending ? "Sending…" : "Send invite"}
              </button>
            </div>
            <small>They get a sign-in link by email. No password to set up.</small>
          </form>
        </section>
        {inviteError && (
          <div className="banner banner-warn" role="alert">
            {inviteError}
          </div>
        )}
        {inviteMessage && (
          <div className="banner banner-ok" role="status">
            {inviteMessage}
          </div>
        )}

        <section className="people-table" aria-labelledby="roster-title">
          <header className="people-table-head">
            <div>
              <h2 id="roster-title">Everyone in the company</h2>
              <p>Admins manage sources, the model, people and actions. Members ask.</p>
            </div>
          </header>
          {rosterError && (
            <div className="banner banner-warn" role="alert">
              {rosterError}
            </div>
          )}
          {rosterMessage && (
            <div className="banner banner-ok" role="status">
              {rosterMessage}
            </div>
          )}
          {members.length === 0 ? (
            <div className="studio-empty">
              <div className="studio-empty-mark" aria-hidden />
              <h3>No one here yet</h3>
              <p className="muted">Send an invite to bring in your first teammate.</p>
            </div>
          ) : (
            (["admin", "member"] as const).map((role) => {
              const group = members.filter((m) => m.role === role);
              if (!group.length) return null;
              return (
                <div key={role} className="people-group">
                  <p className="people-group-title">
                    {role === "admin" ? "Admins" : "Members"} <span>{group.length}</span>
                  </p>
                  <ul className="people-cards">
                    {group.map((m, i) => {
                      const self = m.id === me.user_id;
                      const lastAdmin = m.role === "admin" && adminCount <= 1;
                      return (
                        <li key={m.id} className="person-card" style={{ ...faceStyle(m.id), ["--i" as string]: i }}>
                          <div className="person-card-top">
                            <span className="person-face" aria-hidden>
                              {(m.email || "?").trim().charAt(0).toUpperCase()}
                            </span>
                            <span className="people-role" data-role={m.role}>
                              {m.role === "admin" ? "Admin" : "Member"}
                            </span>
                          </div>
                          <strong className="person-name">
                            {nameFrom(m.email)}
                            {self && <em className="roster-you">you</em>}
                          </strong>
                          <span className="person-email">{m.email}</span>
                          <span className="person-joined">
                            Joined{" "}
                            {new Date(m.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}
                          </span>
                          {!self && (
                            <span className="people-actions">
                              {m.role === "member" ? (
                                <button type="button" disabled={busyId === m.id} onClick={() => handlePromote(m.id, m.email)}>
                                  {busyId === m.id ? "…" : "Make admin"}
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  disabled={busyId === m.id || lastAdmin}
                                  title={lastAdmin ? "Promote someone else before demoting the last admin" : undefined}
                                  onClick={() => handleDemote(m.id, m.email)}
                                >
                                  {busyId === m.id ? "…" : "Demote"}
                                </button>
                              )}
                              <button
                                type="button"
                                className="is-danger"
                                disabled={busyId === m.id || lastAdmin}
                                title={lastAdmin ? "Promote someone else before removing the last admin" : undefined}
                                onClick={() => handleRemove(m.id, m.email)}
                              >
                                Remove
                              </button>
                            </span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              );
            })
          )}
        </section>
      </main>
    </AppShell>
  );
}
