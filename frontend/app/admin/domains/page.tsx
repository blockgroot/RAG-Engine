"use client";

import { useEffect, useState } from "react";
import { Nav } from "@/components/Nav";
import { useMe } from "@/lib/useMe";
import { api, DomainRecord } from "@/lib/api";

export default function DomainsPage() {
  const { me, loading } = useMe();
  const [domains, setDomains] = useState<DomainRecord[]>([]);
  const [newDomain, setNewDomain] = useState("");
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    api.listDomains().then(setDomains);
  }

  useEffect(() => {
    if (me) refresh();
  }, [me]);

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.registerDomain(newDomain.trim().toLowerCase());
      setNewDomain("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not register domain.");
    }
  }

  async function handleAutoJoin(domainId: string, enabled: boolean) {
    await api.setAutoJoin(domainId, enabled);
    refresh();
  }

  if (loading) return null;

  return (
    <>
      <Nav me={me} />
      <main className="page-wide stack">
        <h1>Company domains</h1>
        <p className="muted">
          Add your company&rsquo;s email domain so anyone with that work email can sign in and
          land in your organization automatically. No setup on your end beyond typing it in —
          turn auto-join off any time if you&rsquo;d rather invite people by hand.
        </p>

        <form onSubmit={handleRegister} className="card stack" style={{ maxWidth: "480px" }}>
          <div className="field">
            <label htmlFor="domain">Domain</label>
            <input
              id="domain"
              className="input"
              placeholder="acme.com"
              value={newDomain}
              onChange={(e) => setNewDomain(e.target.value)}
              required
            />
          </div>
          <button className="button" type="submit">Add domain</button>
        </form>

        {error && <p style={{ color: "var(--provenance-none)" }}>{error}</p>}

        <table>
          <thead>
            <tr>
              <th>Domain</th>
              <th>Auto-join</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {domains.map((d) => (
              <tr key={d.id}>
                <td>{d.domain}</td>
                <td>{d.auto_join_enabled ? "on" : "off"}</td>
                <td>
                  <button
                    className="button button-secondary"
                    onClick={() => handleAutoJoin(d.id, !d.auto_join_enabled)}
                  >
                    Turn auto-join {d.auto_join_enabled ? "off" : "on"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </main>
    </>
  );
}
