"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { api, MemberRecord } from "@/lib/api";

export default function MembersPage() {
  const { me, loading } = useMe({ requireAdmin: true });
  const [members, setMembers] = useState<MemberRecord[]>([]);
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  function refresh() {
    api.listMembers().then(setMembers);
  }

  useEffect(() => {
    if (me) refresh();
  }, [me]);

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setSending(true);
    try {
      const invited = await api.inviteMember(email.trim().toLowerCase());
      const who = email.trim().toLowerCase();
      setMessage(
        invited.dev_link
          ? `Invite sent to ${who}. Dev link: ${invited.dev_link}`
          : `Invite sent to ${who}.`
      );
      setEmail("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not invite that email.");
    } finally {
      setSending(false);
    }
  }


  async function handleRevoke(userId: string, email: string) {
    if (busyId) return;
    if (!window.confirm(`Sign out all sessions for ${email}?`)) return;
    setBusyId(userId);
    setError(null);
    setMessage(null);
    try {
      await api.revokeMemberSessions(userId);
      setMessage(`Sessions revoked for ${email}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not revoke sessions.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleRemove(userId: string, email: string) {
    if (busyId) return;
    if (userId === me?.user_id) {
      setError("You cannot remove your own account.");
      return;
    }
    if (!window.confirm(`Remove ${email} from this company? They will lose access immediately.`)) {
      return;
    }
    setBusyId(userId);
    setError(null);
    setMessage(null);
    try {
      await api.removeMember(userId);
      setMessage(`Removed ${email}.`);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove that person.");
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
          description="Invite teammates with their work email — they join your company only."
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
              {error && (
                <div className="banner banner-warn" role="alert">
                  {error}
                </div>
              )}
              {message && (
                <div className="banner banner-ok" role="status">
                  {message}
                </div>
              )}
            </form>
          </section>

          <section className="studio-section people-roster" aria-labelledby="roster-title">
            <div className="studio-section-head">
              <h2 id="roster-title">Team roster</h2>
              <p className="muted">Everyone who can ask in this company.</p>
            </div>
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
                      <span className={`role-chip role-${m.role}`}>{m.role}</span>
                      {m.id !== me.user_id && (
                        <div className="people-card-actions">
                          <button
                            type="button"
                            className="button button-secondary"
                            disabled={busyId === m.id}
                            onClick={() => handleRevoke(m.id, m.email)}
                          >
                            {busyId === m.id ? "…" : "Revoke sessions"}
                          </button>
                          <button
                            type="button"
                            className="button button-secondary"
                            disabled={busyId === m.id}
                            onClick={() => handleRemove(m.id, m.email)}
                          >
                            Remove
                          </button>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>
      </main>
    </AppShell>
  );
}
