"use client";

import { useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { api, MemberRecord } from "@/lib/api";

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
  const memberCount = members.length - adminCount;

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Company"
          title="People"
          description="Invite teammates, make admins for succession, and remove people who leave. Always keep at least one other admin before you leave."
          scene="people"
          meta={
            <>
              <span className="studio-chip">{members.length} people</span>
              <span className="studio-chip studio-chip-ok">{adminCount} admin{adminCount === 1 ? "" : "s"}</span>
              <span className="studio-chip">{memberCount} member{memberCount === 1 ? "" : "s"}</span>
            </>
          }
        />

        <div className="people-layout">
          <section className="studio-panel invite-panel" aria-labelledby="invite-title">
            <div className="studio-panel-glow" aria-hidden />
            <div className="studio-section-head">
              <h2 id="invite-title">Invite someone</h2>
              <p className="muted">They get a magic link — no password to set up.</p>
            </div>
            <form onSubmit={handleInvite} className="invite-form">
              <div className="field">
                <label htmlFor="email">Work email</label>
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
              </div>
              <button className="button" type="submit" disabled={sending || !email.trim()}>
                {sending ? "Sending…" : "Send invite"}
              </button>
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
            </form>
          </section>

          <section className="roster-board" aria-labelledby="roster-title">
            <div className="studio-section-head roster-board-head">
              <h2 id="roster-title">Team roster</h2>
              <p className="muted">Everyone who can ask in this company.</p>
            </div>
            {rosterError && (
              <div className="banner banner-warn" role="alert" style={{ marginBottom: "0.75rem" }}>
                {rosterError}
              </div>
            )}
            {rosterMessage && (
              <div className="banner banner-ok" role="status" style={{ marginBottom: "0.75rem" }}>
                {rosterMessage}
              </div>
            )}
            <div className="roster-scroll">
            {members.length === 0 ? (
              <div className="studio-empty">
                <div className="studio-empty-mark" aria-hidden />
                <h3>No one here yet</h3>
                <p className="muted">Send an invite to bring in your first teammate.</p>
              </div>
            ) : (
              <ul className="people-grid">
                {members.map((m, i) => {
                  const initial = (m.email || "?").trim().charAt(0).toUpperCase();
                  return (
                    <li
                      key={m.id}
                      className="people-card"
                      style={{ animationDelay: `${0.08 + i * 0.05}s` }}
                    >
                      <span className="people-avatar" aria-hidden>
                        {initial}
                      </span>
                      <div className="people-card-copy">
                        <strong>{m.email}</strong>
                        <span className="muted">
                          Joined {new Date(m.created_at).toLocaleDateString()}
                        </span>
                      </div>
                      <div className="people-card-meta">
                      <span className={`role-chip role-${m.role}`}>{m.role}</span>
                      <div className="people-card-actions">
                        {m.role === "member" && (
                          <button
                            type="button"
                            className="button button-secondary"
                            disabled={busyId === m.id}
                            onClick={() => handlePromote(m.id, m.email)}
                          >
                            {busyId === m.id ? "…" : "Make admin"}
                          </button>
                        )}
                        {m.role === "admin" && m.id !== me.user_id && (
                          <button
                            type="button"
                            className="button button-secondary"
                            disabled={busyId === m.id || adminCount <= 1}
                            title={
                              adminCount <= 1
                                ? "Promote someone else before demoting the last admin"
                                : undefined
                            }
                            onClick={() => handleDemote(m.id, m.email)}
                          >
                            {busyId === m.id ? "…" : "Demote"}
                          </button>
                        )}
                        {m.id !== me.user_id && (
                          <button
                            type="button"
                            className="button button-secondary"
                            disabled={busyId === m.id || (m.role === "admin" && adminCount <= 1)}
                            title={
                              m.role === "admin" && adminCount <= 1
                                ? "Promote someone else before removing the last admin"
                                : undefined
                            }
                            onClick={() => handleRemove(m.id, m.email)}
                          >
                            Remove
                          </button>
                        )}
                      </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
            </div>
          </section>
        </div>
      </main>
    </AppShell>
  );
}
