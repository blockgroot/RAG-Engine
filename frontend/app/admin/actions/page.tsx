"use client";

/**
 * Admin-only: which actions Handbook may take, and every one it has taken.
 *
 * Everything starts off. Turning an action on lets people ASK for it; each
 * write still needs that person's own approval and their own connected
 * account, so this page never grants anyone more than they already have in
 * the tool. The log is the record: who asked, what was sent, where, and how
 * it ended.
 */

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { ApiError, api, type AdminActions } from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  proposed: "Waiting",
  executing: "Sending",
  succeeded: "Done",
  failed: "Failed",
  unknown: "Unknown",
  rejected: "Cancelled",
  expired: "Expired",
};

export default function AdminActionsPage() {
  const { me, loading } = useMe({ requireAdmin: true });
  const [data, setData] = useState<AdminActions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.adminActions());
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

  async function toggle(name: string, enabled: boolean) {
    setBusy(name);
    setError(null);
    try {
      await api.setAdminAction(name, enabled);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change that.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Company"
          title="Actions"
          description="Choose what people may ask Handbook to do in your tools. Every write is shown to the person first, sent only when they approve, and sent with their own account."
        />

        {error && (
          <div className="banner banner-warn" role="alert">
            {error}
          </div>
        )}

        {data && !data.enabled && (
          <div className="banner" role="status">
            Act is switched off for this Handbook installation (<code>ACTIONS_ENABLED</code>).
          </div>
        )}

        {data && (
          <section className="studio-panel" aria-labelledby="actions-catalogue">
            <div className="studio-section-head">
              <h2 id="actions-catalogue">What people may ask for</h2>
              <p className="muted">
                Create and comment only. Handbook cannot edit, close, delete, merge or push code.
                GitHub writes also need your GitHub admin to accept the App&rsquo;s Issues and Pull
                requests write permissions.
              </p>
            </div>
            <ul className="act-catalogue">
              {data.catalogue.map((a) => (
                <li key={a.name} className="act-catalogue-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={a.enabled}
                      disabled={!data.enabled || busy === a.name}
                      onChange={(e) => toggle(a.name, e.target.checked)}
                    />{" "}
                    {a.label}
                  </label>
                </li>
              ))}
            </ul>
            <p className="muted">
              {data.block_wider
                ? "Drafts that would show private documents to people who can't see them are refused."
                : "Drafts that would show private documents to people who can't see them need the person to tick an acknowledgement. Private content is never sent anywhere public."}
            </p>
          </section>
        )}

        {data && data.enabled && (
          <section className="studio-panel" aria-labelledby="actions-log">
            <div className="studio-section-head">
              <h2 id="actions-log">Log</h2>
              <p className="muted">The last 100 actions anyone asked for, newest first.</p>
            </div>
            {data.log.length === 0 ? (
              <p className="muted">Nothing yet.</p>
            ) : (
              <div className="act-log-wrap">
                <table className="act-log">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Who</th>
                      <th>What</th>
                      <th>Where</th>
                      <th>Private content</th>
                      <th>Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.log.map((r) => (
                      <tr key={r.id}>
                        <td>{new Date(r.created_at).toLocaleString()}</td>
                        <td>{r.user_email ?? "(removed)"}</td>
                        <td>{r.label}</td>
                        <td>{r.target.name}</td>
                        <td>
                          {r.exposure === "wider" || r.exposure === "unknown"
                            ? `${r.exposure}${r.acknowledged ? ", acknowledged" : ""}`
                            : "—"}
                        </td>
                        <td>
                          {r.result?.url && r.status === "succeeded" ? (
                            <a href={r.result.url} target="_blank" rel="noreferrer">
                              {STATUS_LABEL[r.status]}
                            </a>
                          ) : (
                            STATUS_LABEL[r.status] ?? r.status
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        )}
      </main>
    </AppShell>
  );
}
