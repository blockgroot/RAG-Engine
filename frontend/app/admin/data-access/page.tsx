"use client";

/**
 * Admin-only: the company's read-only database login.
 *
 * Says plainly that this login reads every indexed document, private ones
 * included: Handbook shows each person only what is shared with them, and an
 * admin who believed the login kept that rule would hand it out on a false
 * belief. The password is shown once, right after it is made, and never again.
 */

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { ApiError, api, type DataAccess, type DataAccessCredentials } from "@/lib/api";

/** One address or range per line, or separated by commas. */
function splitIps(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function AdminDataAccessPage() {
  const { me, loading } = useMe({ requireAdmin: true });

  const [data, setData] = useState<DataAccess | null>(null);
  const [credentials, setCredentials] = useState<DataAccessCredentials | null>(null);
  const [ips, setIps] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [copied, setCopied] = useState(false);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.getDataAccess();
      setData(next);
      setIps(next.login ? next.login.allowed_ips.join("\n") : "");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load this page.");
    }
  }, []);

  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading&hellip;</p>
      </main>
    );
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That did not work. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const create = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      setCredentials(await api.createDataAccess(splitIps(ips), acknowledged));
      setCopied(false);
      await load();
    });
  };

  const saveIps = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api.setDataAccessIps(splitIps(ips));
      await load();
      setNotice("Allowed addresses saved.");
    });
  };

  const resetPassword = () => {
    if (!window.confirm("Make a new password? The current one stops working at once.")) return;
    void run(async () => {
      setCredentials(await api.resetDataAccessPassword());
      setCopied(false);
    });
  };

  const turnOff = () => {
    if (!window.confirm("Turn off database access? The login stops working at once.")) return;
    void run(async () => {
      await api.deleteDataAccess();
      setCredentials(null);
      setAcknowledged(false);
      await load();
      setNotice("Database access is off. The login has been deleted.");
    });
  };

  const copyUri = async () => {
    if (!credentials) return;
    try {
      await navigator.clipboard.writeText(credentials.uri);
      setCopied(true);
    } catch {
      setError("Could not copy. Select the address and copy it by hand.");
    }
  };

  const login = data?.login ?? null;

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack model-admin-page">
        <PageHeader
          eyebrow="Company"
          title="Database access"
          description="Give your company a read-only database login to query its own Handbook data with SQL tools."
          meta={
            !data ? null : login ? (
              <span className="studio-chip studio-chip-ok">Login active</span>
            ) : (
              <span className="studio-chip">{data.enabled ? "Not set up" : "Not available"}</span>
            )
          }
        />

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

        {credentials && (
          <section className="studio-panel model-current-panel" aria-labelledby="creds-title">
            <div className="studio-section-head">
              <p className="model-section-label">Shown once</p>
              <h2 id="creds-title">Your connection address</h2>
            </div>
            <div className="banner banner-warn" role="note">
              Copy it now. Handbook does not keep the password, so it cannot show it again. If it is
              lost, make a new one.
            </div>
            <p className="mono">{credentials.uri}</p>
            <div className="model-form-actions">
              <span className="muted">
                Login <span className="mono">{credentials.role_name}</span>. It needs SSL and only
                works from your allowed addresses.
              </span>
              <button className="button" type="button" onClick={copyUri}>
                {copied ? "Copied" : "Copy address"}
              </button>
            </div>
          </section>
        )}

        {data && !data.enabled && !login && (
          <section className="studio-panel" aria-labelledby="off-title">
            <h2 id="off-title">Not available yet</h2>
            <p className="muted">
              Database access is not set up on this Handbook. Contact Handbook support to turn it on.
            </p>
          </section>
        )}

        {login && (
          <section className="studio-panel model-current-panel" aria-labelledby="current-title">
            <div className="studio-section-head">
              <p className="model-section-label">Current login</p>
              <h2 id="current-title">Read-only access is on</h2>
            </div>
            <div className="model-current">
              <div className="model-current-identity">
                <span className="model-current-name mono">{login.role_name}</span>
                <span>Created {new Date(login.created_at).toLocaleDateString()}</span>
              </div>
              <button className="button button-secondary" onClick={resetPassword} disabled={busy || !data?.enabled}>
                New password
              </button>
              <button className="button button-secondary" onClick={turnOff} disabled={busy}>
                Turn off access
              </button>
            </div>

            <form onSubmit={saveIps} className="model-form">
              <div className="field field-wide">
                <label htmlFor="ips">Allowed addresses</label>
                <textarea
                  id="ips"
                  className="input"
                  rows={4}
                  value={ips}
                  onChange={(e) => setIps(e.target.value)}
                  disabled={busy}
                />
                <p className="muted">One IP address or range per line. Connections from anywhere else are refused.</p>
              </div>
              <div className="model-form-actions">
                <span />
                <button className="button" type="submit" disabled={busy || splitIps(ips).length === 0}>
                  Save addresses
                </button>
              </div>
            </form>
          </section>
        )}

        {data?.enabled && !login && (
          <section className="studio-panel model-setup-panel" aria-labelledby="setup-title">
            <header className="model-setup-head">
              <div>
                <p className="model-section-label">Set up</p>
                <h2 id="setup-title">Create a read-only login</h2>
                <p>One login for your company. You can make a new password or turn it off at any time.</p>
              </div>
              <span>2-step setup</span>
            </header>

            <form onSubmit={create} className="model-form">
              <div className="field field-wide">
                <label htmlFor="new-ips"><span aria-hidden>1</span> Allowed addresses</label>
                <textarea
                  id="new-ips"
                  className="input"
                  rows={4}
                  value={ips}
                  onChange={(e) => setIps(e.target.value)}
                  placeholder={"203.0.113.7\n198.51.100.0/24"}
                  disabled={busy}
                  required
                />
                <p className="muted">
                  The IP addresses your SQL tools connect from: your office, VPN or reporting server.
                  One per line; at most 20.
                </p>
              </div>

              <div className="field field-wide">
                <label htmlFor="ack"><span aria-hidden>2</span> What this login can read</label>
                {/* A div, not a label: the form styles every span directly in a label as a step number. */}
                <div className="action-surface">
                  <span>
                    <strong>All of your company&rsquo;s indexed data, including private documents.</strong>
                    <small>
                      In Handbook, each person sees only what is shared with them. This login does not
                      follow that rule: whoever has it can read every synced document, private Drive
                      files and private Slack channels included.
                    </small>
                  </span>
                  <input
                    id="ack"
                    type="checkbox"
                    checked={acknowledged}
                    onChange={(e) => setAcknowledged(e.target.checked)}
                    disabled={busy}
                  />
                </div>
              </div>

              <div className="model-form-actions">
                <span className="muted">The password is shown once, right after the login is made.</span>
                <button
                  className="button"
                  type="submit"
                  disabled={busy || !acknowledged || splitIps(ips).length === 0}
                >
                  {busy ? "Creating…" : "Create login"}
                </button>
              </div>
            </form>
          </section>
        )}

        <section className="studio-panel" aria-labelledby="how-title">
          <h2 id="how-title">How the login works</h2>
          <ul className="muted">
            <li>It reads your company&rsquo;s document passages (the <span className="mono">chunks</span> table) and nothing else. Other companies&rsquo; data is never visible.</li>
            <li>It is read-only. It connects to a copy of the database, so heavy queries never slow Handbook down.</li>
            <li>Connections need SSL and must come from your allowed addresses. The number of connections is capped, and long queries time out.</li>
            <li>New documents appear within seconds of syncing. Turning access off stops it returning any data at once.</li>
          </ul>
        </section>
      </main>
    </AppShell>
  );
}
