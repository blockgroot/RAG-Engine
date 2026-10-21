"use client";

/**
 * Admin-only: whether the company uses Act, and which actions people may ask for.
 *
 * Everything starts off. Turning an action on lets people ASK for it; each
 * write still needs that person's own approval and their own connected
 * account, so this page never grants anyone more than they already have in
 * the tool. There is no log here on purpose: what a person drafted is theirs,
 * and each tool records what was created, by whom.
 */

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { McpServersAdmin } from "@/components/McpServersAdmin";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { ApiError, api, type AdminActions } from "@/lib/api";

const PROVIDERS = [
  ["linear", "Linear", "Issues, sub-issues and comments, as the person"],
  ["github", "GitHub", "Issues and comments, as the person (never code)"],
  ["notion", "Notion", "Pages, comments and database rows"],
  ["google", "Google Docs & Sheets", "New Docs and Sheets, private to the person who asked"],
  ["slack", "Slack", "Channel posts, thread replies and DMs"],
] as const;

type Mode = "off" | "on";

const MODES: { value: Mode; label: string; hint: string }[] = [
  { value: "off", label: "Off", hint: "Handbook only reads." },
  {
    value: "on",
    label: "On",
    hint: "On company-wide and in every space. A space's owner can turn it off for their space.",
  },
];

const SURFACE_COPY: Record<string, string> = {
  "surface:slack_dm": "People can ask Agent mode privately in a DM with Handbook.",
  "surface:slack_channel": "People can ask Agent mode in a channel; any Slack post stays in that thread.",
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

  const mode: Mode = data?.enabled ? "on" : "off";

  async function setMode(next: Mode) {
    setBusy("act");
    setError(null);
    try {
      await api.setAdminAction("act", next !== "off");
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

        {data && (
          <section className="studio-panel stack" aria-labelledby="actions-allow">
            <div className="studio-section-head">
              <h2 id="actions-allow">Agent mode for your company</h2>
              <p className="muted">
                People ask Handbook to file issues, comment and post, using their own accounts, and
                approve each one. Space owners can only narrow what you allow here.
              </p>
            </div>
            <div className="act-mode-options" role="radiogroup" aria-label="Agent mode for your company">
              {MODES.map((m) => (
                <label key={m.value} className="act-mode-option" data-checked={mode === m.value}>
                  <input
                    type="radio"
                    name="act-mode"
                    value={m.value}
                    checked={mode === m.value}
                    disabled={busy === "act"}
                    onChange={() => setMode(m.value)}
                  />
                  <span>
                    <strong>{m.label}</strong>
                    <small>{m.hint}</small>
                  </span>
                </label>
              ))}
            </div>
          </section>
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
              <h2 id="actions-surfaces">Agent mode in Slack</h2>
              <p className="muted">Agent mode in the web chat is always available. Choose whether requests may also start in Slack.</p>
            </div>
            <div className="actions-surface-list">
              {data.surfaces.map((surface) => (
                <label key={surface.name} className="action-surface">
                  <span>
                    <strong>{surface.label.replace(/^Allow (Agent mode|Act) from /, "")}</strong>
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

        {data && <McpServersAdmin disabled={!data.enabled} />}
      </main>
    </AppShell>
  );
}
