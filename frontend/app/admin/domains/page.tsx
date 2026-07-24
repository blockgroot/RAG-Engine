"use client";

import { useEffect, useState } from "react";
import { Nav } from "@/components/Nav";
import { useMe } from "@/lib/useMe";
import { api, DomainRecord, DomainRegistration } from "@/lib/api";

export default function DomainsPage() {
  const { me, loading } = useMe();
  const [domains, setDomains] = useState<DomainRecord[]>([]);
  const [newDomain, setNewDomain] = useState("");
  const [pending, setPending] = useState<DomainRegistration | null>(null);
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
      const instructions = await api.registerDomain(newDomain.trim().toLowerCase());
      setPending(instructions);
      setNewDomain("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not register domain.");
    }
  }

  async function handleVerify(domainId: string) {
    setError(null);
    const { verified } = await api.verifyDomain(domainId);
    if (!verified) {
      setError("DNS record not found yet — publishing can take a few minutes to propagate.");
    }
    refresh();
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
          Verify your company&rsquo;s email domain so employees can sign in with a work email and
          land in the right organization — automatically, but only once you&rsquo;ve proven you
          control the domain and explicitly turned auto-join on.
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
          <button className="button" type="submit">Register</button>
        </form>

        {error && <p style={{ color: "var(--provenance-none)" }}>{error}</p>}

        {pending && (
          <div className="card stack">
            <p>Publish a DNS TXT record, then verify:</p>
            <p className="mono">{pending.dns_record_name}</p>
            <p className="mono">{pending.dns_record_value}</p>
          </div>
        )}

        <table>
          <thead>
            <tr>
              <th>Domain</th>
              <th>Status</th>
              <th>Auto-join</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {domains.map((d) => (
              <tr key={d.id}>
                <td>{d.domain}</td>
                <td>
                  <span className={`badge ${d.verified ? "badge-verified" : "badge-pending"}`}>
                    {d.verified ? "verified" : "unverified"}
                  </span>
                </td>
                <td>{d.auto_join_enabled ? "on" : "off"}</td>
                <td>
                  {!d.verified && (
                    <button className="button button-secondary" onClick={() => handleVerify(d.id)}>
                      Verify
                    </button>
                  )}
                  {d.verified && (
                    <button
                      className="button button-secondary"
                      onClick={() => handleAutoJoin(d.id, !d.auto_join_enabled)}
                    >
                      Turn auto-join {d.auto_join_enabled ? "off" : "on"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </main>
    </>
  );
}
