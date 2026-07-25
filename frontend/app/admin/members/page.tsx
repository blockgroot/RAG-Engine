"use client";

import { useEffect, useState } from "react";
import { Nav } from "@/components/Nav";
import { useMe } from "@/lib/useMe";
import { api, MemberRecord } from "@/lib/api";

export default function MembersPage() {
  const { me, loading } = useMe();
  const [members, setMembers] = useState<MemberRecord[]>([]);
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    api.listMembers().then(setMembers);
  }

  useEffect(() => {
    if (me) refresh();
  }, [me]);

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.inviteMember(email.trim().toLowerCase());
      setEmail("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not invite that email.");
    }
  }

  if (loading) return null;

  return (
    <>
      <Nav me={me} />
      <main className="page-wide stack">
        <h1>Team members</h1>
        <p className="muted">
          Invite a teammate by their email — they&rsquo;ll be able to request a sign-in link
          and land directly in your organization. No domain setup needed.
        </p>

        <form onSubmit={handleInvite} className="card stack" style={{ maxWidth: "480px" }}>
          <div className="field">
            <label htmlFor="email">Email</label>
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
          <button className="button" type="submit">Invite</button>
        </form>

        {error && <p style={{ color: "var(--provenance-none)" }}>{error}</p>}

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
                <td>{m.role}</td>
                <td>{new Date(m.created_at).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </main>
    </>
  );
}
