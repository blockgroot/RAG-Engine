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

const PROVIDERS = [
  ["linear", "Linear", "Issues and comments"],
  ["github", "GitHub", "Issues and pull-request comments"],
  ["notion", "Notion", "Pages, page content and comments"],
  ["google", "Google Drive", "New documents in a person’s Drive"],
  ["slack", "Slack", "Messages from the Handbook bot"],
] as const;

const SURFACE_COPY: Record<string, string> = {
  "surface:slack_dm": "People can ask privately using “act …” in a DM with Handbook.",
  "surface:slack_channel": "People can ask in a channel; any Slack post stays in that thread.",
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

  const providerGroups = PROVIDERS.map(([key, label, description]) => ({
    key,
    label,
    description,
    actions: data?.catalogue.filter((action) => action.provider === key) ?? [],
  })).filter((group) => group.actions.length > 0);

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack actions-admin-page">
        <PageHeader
          eyebrow="Company"
          title="Action permissions"
          description="Choose where Handbook may write. These settings allow requests—not automatic changes."
        />

        {error && (
          <div className="banner banner-warn" role="alert">
            {error}
          </div>
        )}

        {data && !data.enabled && (
          <div className="banner" role="status">
            Act is off for this installation. Turn on <code>ACTIONS_ENABLED</code> before configuring permissions.
          </div>
        )}

        {data && (
          <div className="action-safety-note" role="note">
            <strong>People stay in control.</strong>
            <span>Every draft is reviewed and approved by the person who requested it, using their own connected account.</span>
          </div>
        )}

        {data && (
          <section className="studio-panel actions-permissions-panel" aria-labelledby="actions-catalogue">
            <div className="studio-section-head">
              <h2 id="actions-catalogue">Allowed actions</h2>
              <p className="muted">
                Turn on only the changes people should be able to request. Everything starts off.
              </p>
            </div>
            <div className="actions-provider-grid">
              {providerGroups.map((group) => {
                const enabled = group.actions.filter((action) => action.enabled).length;
                return (
                  <article key={group.key} className="actions-provider-group">
                    <header>
                      <div>
                        <h3>{group.label}</h3>
                        <p>{group.description}</p>
                      </div>
                      <span>{enabled}/{group.actions.length} on</span>
                    </header>
                    <ul>
                      {group.actions.map((action) => (
                        <li key={action.name}>
                          <label className="action-toggle">
                            <span>{action.label.replace(/^.*?:\s*/, "")}</span>
                            <input
                              type="checkbox"
                              checked={action.enabled}
                              disabled={!data.enabled || busy === action.name}
                              onChange={(event) => toggle(action.name, event.target.checked)}
                              aria-label={`${action.enabled ? "Disable" : "Enable"} ${action.label}`}
                            />
                          </label>
                        </li>
                      ))}
                    </ul>
                  </article>
                );
              })}
            </div>
          </section>
        )}

        {data && (
          <section className="studio-panel actions-slack-panel" aria-labelledby="actions-surfaces">
            <div className="studio-section-head">
              <h2 id="actions-surfaces">Act in Slack</h2>
              <p className="muted">The Act page is always available. Choose whether requests may also start in Slack.</p>
            </div>
            <div className="actions-surface-list">
              {data.surfaces.map((surface) => (
                <label key={surface.name} className="action-surface">
                  <span>
                    <strong>{surface.label.replace(/^Allow Act from /, "")}</strong>
                    <small>{SURFACE_COPY[surface.name]}</small>
                  </span>
                  <input
                    type="checkbox"
                    checked={surface.enabled}
                    disabled={!data.enabled || busy === surface.name}
                    onChange={(event) => toggle(surface.name, event.target.checked)}
                    aria-label={`${surface.enabled ? "Disable" : "Enable"} ${surface.label}`}
                  />
                </label>
              ))}
            </div>
            <p className="actions-privacy-note">
              <strong>Private content stays protected.</strong>{" "}
              {data.block_wider
                ? "A draft is refused when its destination may include people who cannot see its private sources."
                : "A person must acknowledge wider sharing before approval. Private content is never sent to a public destination."}
            </p>
          </section>
        )}

        {data && data.enabled && (
          <section className="studio-panel actions-activity-panel" aria-labelledby="actions-log">
            <div className="studio-section-head">
              <h2 id="actions-log">Recent activity</h2>
              <p className="muted">Requests and outcomes across your company.</p>
            </div>
            {data.log.length === 0 ? (
              <div className="actions-empty">
                <strong>No actions yet</strong>
                <span>Completed, cancelled and failed requests will appear here.</span>
              </div>
            ) : (
              <div className="actions-log-list">
                {data.log.map((row) => (
                  <article key={row.id} className="actions-log-row">
                    <div className="actions-log-main">
                      <strong>{row.label}</strong>
                      <span>{row.target.name}</span>
                    </div>
                    <div className="actions-log-meta">
                      <span>{row.user_email ?? "Removed user"}</span>
                      <time dateTime={row.created_at}>{new Date(row.created_at).toLocaleString()}</time>
                    </div>
                    <div className="actions-log-result">
                      {row.exposure === "wider" || row.exposure === "unknown" ? (
                        <small>{row.acknowledged ? "Sharing acknowledged" : "Sharing review needed"}</small>
                      ) : null}
                      {row.result?.url && row.status === "succeeded" ? (
                        <a href={row.result.url} target="_blank" rel="noreferrer">
                          {STATUS_LABEL[row.status]}
                        </a>
                      ) : (
                        <span data-status={row.status}>{STATUS_LABEL[row.status] ?? row.status}</span>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        )}
      </main>
    </AppShell>
  );
}
