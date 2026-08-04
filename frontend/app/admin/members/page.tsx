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
        <PageHeader
          eyebrow="Company"
          title="People"
          description="Invite people with their work email."
        />

        <section className="panel">
          <div className="panel-head">
            <h2>Invite</h2>
          </div>
          <form onSubmit={handleInvite} className="stack" style={{ maxWidth: "480px" }}>
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
            <button className="button" type="submit" style={{ width: "fit-content" }}>
              Send invite
            </button>
          </form>
        </section>

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
