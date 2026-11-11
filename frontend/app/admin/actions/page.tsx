"use client";

/**
 * Admin-only: whether the company uses Agent mode, which tools it may use
 * (each through its MCP server), and where people may ask from.
 *
 * Everything starts off. Turning a tool on lets people ASK for it; each write
 * still needs that person's own approval and their own sign-in, so this page
 * never grants anyone more than they already have in the tool. There is no log
 * here on purpose: what a person drafted is theirs, and each tool records what
 * was created, by whom.
 */

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { BrandGlyph } from "@/components/BrandGlyph";
import { McpServersAdmin } from "@/components/McpServersAdmin";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { ApiError, api, type AdminActions } from "@/lib/api";

type Mode = "off" | "on";

/** What happens to every request, said once, as three steps. */
const FLOW = [
  { n: "1", title: "Someone asks", text: "“File a bug for this”, in chat or Slack." },
  { n: "2", title: "Handbook drafts it", text: "Using only the tools you turn on below." },
  { n: "3", title: "They approve it", text: "From their own account. Nothing runs by itself." },
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

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack actions-admin-page" data-off={data ? !data.enabled : undefined}>
        <PageHeader
          eyebrow="Company"
          title="Agent mode"
          description="What Handbook may do for people. Always with their approval."
        />

        {error && (
          <div className="banner banner-warn" role="alert">
            {error}
          </div>
        )}

        {data && (
          <section className="studio-panel agent-master" data-on={mode === "on"} aria-labelledby="actions-allow">
            <div className="agent-master-head">
              <span className="agent-master-icon" aria-hidden>
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                  <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
                </svg>
              </span>
              <div className="agent-master-copy">
                <h2 id="actions-allow">
                  Agent mode is {mode === "on" ? "on" : "off"}
                </h2>
                <p>
                  {mode === "on"
                    ? "People can ask Handbook to file issues, comment and post. A space's owner can turn it off for their space."
                    : "Handbook only reads. Turn it on to let people ask it to file issues, comment and post."}
                </p>
              </div>
              <label className="agent-switch">
                <input
                  type="checkbox"
                  role="switch"
                  checked={mode === "on"}
                  disabled={busy === "act"}
                  onChange={(e) => setMode(e.target.checked ? "on" : "off")}
                  aria-label="Agent mode for your company"
                />
                <span aria-hidden />
              </label>
            </div>
            <ol className="agent-flow">
              {FLOW.map((step) => (
                <li key={step.n}>
                  <b>{step.n}</b>
                  <span>
                    <strong>{step.title}</strong>
                    <small>{step.text}</small>
                  </span>
                </li>
              ))}
            </ol>
          </section>
        )}

        {data && <McpServersAdmin disabled={!data.enabled} />}

        {data && (
          <section className="studio-panel actions-slack-panel" aria-labelledby="actions-surfaces">
            <div className="studio-section-head">
              <h2 id="actions-surfaces">Agent mode in Slack</h2>
              <p className="muted">Agent mode in the web chat is always available. Choose whether requests may also start in Slack.</p>
            </div>
            <div className="actions-surface-list">
              {data.surfaces.map((surface) => (
                <label key={surface.name} className="action-surface">
                  <span className="action-surface-mark" aria-hidden>
                    <BrandGlyph name="slack" size={18} />
                  </span>
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
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
                <rect x="5" y="11" width="14" height="9" rx="2" stroke="currentColor" strokeWidth="2" />
                <path d="M8 11V8a4 4 0 0 1 8 0v3" stroke="currentColor" strokeWidth="2" />
              </svg>
              <strong>Private content stays protected.</strong>{" "}
              {data.block_wider
                ? "A draft is refused when its destination may include people who cannot see its private sources."
                : "A person must acknowledge wider sharing before approval. Private content is never sent to a public destination."}
            </p>
          </section>
        )}

      </main>
    </AppShell>
  );
}
