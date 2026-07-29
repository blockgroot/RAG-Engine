"use client";

import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { useMe } from "@/lib/useMe";
import { api, MemberRecord } from "@/lib/api";

export default function MembersPage() {
  const { me, loading } = useMe({ requireAdmin: true });
  const [members, setMembers] = useState<MemberRecord[]>([]);
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

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
    }
  }

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide stack">
        <div>
          <p className="eyebrow">Admin</p>
          <h1>Team members</h1>
          <p className="muted">
            Invite teammates by email. They get Ask access for your organization.
          </p>
        </div>

        <form onSubmit={handleInvite} className="card stack" style={{ maxWidth: "480px" }}>
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
            />
          </div>
          <button className="button" type="submit">
            Send invite
          </button>
        </form>

        {error && <div className="banner banner-warn">{error}</div>}
        {message && <div className="banner banner-ok">{message}</div>}

        <table>
          <thead>
            <tr>
              <th>Email</th>
              <th>Role</th>
              <th>Added</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td>{m.email}</td>
                <td>
                  <span className={`role-chip role-${m.role}`}>{m.role}</span>
                </td>
                <td>{new Date(m.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </main>
    </AppShell>
  );
}
