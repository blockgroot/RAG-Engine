"use client";

/**
 * Act on the Account page: the accounts Handbook writes with for this person,
 * and what they asked it to do.
 *
 * A write runs as the person's OWN account, so it has their rights and shows
 * them as its author; this is where they connect and disconnect those
 * accounts. Requests are made in the chat (+ then Act). Drafts still waiting
 * can be approved, edited or cancelled from the history here.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { ActionCard } from "@/components/ActionCard";
import { ApiError, api, type ActCard, type ActStatus, type McpServerMine } from "@/lib/api";

const PROVIDER_LABEL: Record<string, string> = {
  linear: "Linear",
  github: "GitHub",
  notion: "Notion",
  google: "Google Docs & Sheets",
  slack: "Slack",
  mcp: "That tool",
};

/** Why a connect was refused. Each is about keeping company content inside
 *  the company: Handbook only writes into the company's own workspace. */
const CONNECT_ERROR: Record<string, string> = {
  workspace:
    "That account is in a different workspace from your company's. Connect the account you use for work.",
  account:
    "That account isn't your work account. Connect the one that uses the email you sign in to Handbook with.",
};

const HISTORY_STATUS: Record<ActCard["status"], string> = {
  proposed: "Waiting",
  executing: "Sending",
  succeeded: "Done",
  failed: "Failed",
  unknown: "Check tool",
  rejected: "Cancelled",
  expired: "Expired",
};

function ActionHistoryRow({ action }: { action: ActCard }) {
  // The label names the app ("Google Sheets: create a new sheet"): a Doc or
  // Sheet is stored in Drive but opens in its own app.
  const provider =
    action.label.split(":")[0] || PROVIDER_LABEL[action.provider ?? ""] || action.provider || "tool";
  const actionName = action.label.replace(/^.*?:\s*/, "");
  return (
    <article className="account-action-row">
      <div className="account-action-copy">
        <span className="account-action-provider">{provider}</span>
        <strong>{actionName}</strong>
        <span className="account-action-target">
          {action.payload.title || action.target.name}
        </span>
      </div>
      <time className="account-action-date" dateTime={action.created_at}>
        {new Date(action.created_at).toLocaleDateString(undefined, {
          day: "numeric",
          month: "short",
          year: "numeric",
        })}
      </time>
      <div className="account-action-outcome">
        <span className="account-action-status" data-status={action.status}>
          {HISTORY_STATUS[action.status]}
        </span>
        {action.status === "succeeded" && action.result?.url ? (
          <a
            className="account-action-link"
            href={action.result.url}
            target="_blank"
            rel="noreferrer"
          >
            Open in {provider}
            <span aria-hidden="true">↗</span>
          </a>
        ) : null}
      </div>
    </article>
  );
}

export function ActPanel() {
  const [status, setStatus] = useState<ActStatus | null>(null);
  const [mcp, setMcp] = useState<McpServerMine[]>([]);
  const [recent, setRecent] = useState<ActCard[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [next, other] = await Promise.all([api.actStatus(), api.myMcp().catch(() => ({ servers: [] }))]);
      setStatus(next);
      setMcp(other.servers);
      setRecent(next.recent ?? []);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load Agent settings.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Back from connecting an account: say how it went, then tidy the URL.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("connected");
    const failed = params.get("connect_error");
    if (connected)
      setNotice(
        `${PROVIDER_LABEL[connected] ?? connected} is connected. Handbook can now act for you there, after you approve.`,
      );
    if (failed) {
      setError(
        CONNECT_ERROR[params.get("reason") ?? ""] ??
          `Couldn’t connect ${PROVIDER_LABEL[failed] ?? failed}. Try again.`,
      );
    }
    if (connected || failed)
      window.history.replaceState(null, "", "/account#act");
  }, []);

  async function disconnectMcp(id: string) {
    setError(null);
    try {
      await api.disconnectMcp(id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not disconnect.");
    }
  }

  async function disconnect(provider: string) {
    setError(null);
    try {
      await api.disconnectActProvider(provider);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not disconnect.");
    }
  }

  function updated(next: ActCard) {
    setRecent((all) => [next, ...all.filter((a) => a.id !== next.id)]);
  }

  const shownProviders = (status?.providers ?? []).filter(
    (p) => p.needed || p.connected || p.needs_reauth,
  );

  if (status && !status.enabled) return null;

  return (
    <section
      id="act"
      className="studio-panel stack act-panel"
      aria-labelledby="act-title"
    >
      <div className="act-panel-head">
        <div className="studio-section-head">
          <h2 id="act-title">Agent</h2>
          <p className="muted">
            The accounts Handbook writes with for you, after you approve each
            draft. Ask in the chat.
          </p>
        </div>
        <Link
          className="button button-secondary button-sm"
          href="/chat?mode=act"
        >
          Open Agent mode in chat
        </Link>
      </div>

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

      {status && status.actions.length === 0 && mcp.length === 0 ? (
        <p className="muted">
          Your admin hasn&rsquo;t switched on any actions yet.
        </p>
      ) : status ? (
        <>
          <div className="account-act-accounts">
            <div className="account-act-section-head">
              <h3>Connected accounts</h3>
              <p>Actions are sent using your identity in each tool.</p>
            </div>
            <ul className="act-providers">
              {shownProviders.map((p) => (
                <li key={p.provider} className="act-provider">
                  <div className="act-provider-copy">
                    <span className="act-provider-name">
                      {PROVIDER_LABEL[p.provider] ?? p.provider}
                    </span>
                    {!p.available && !p.connected ? (
                      <span className="act-status-note">
                        Admin setup required
                      </span>
                    ) : p.connected ? (
                      <span className="act-status-ready">
                        Connected as {p.account}
                      </span>
                    ) : (
                      <span className="act-status-note">
                        {p.needs_reauth ? "Connection expired" : "Not connected"}
                      </span>
                    )}
                  </div>
                  {p.connected ? (
                    <button
                      type="button"
                      className="act-text-button"
                      onClick={() => disconnect(p.provider)}
                    >
                      Disconnect
                    </button>
                  ) : p.available ? (
                    <a
                      className="button button-secondary button-sm"
                      href={api.actConnectUrl(p.provider)}
                    >
                      {p.needs_reauth ? "Reconnect" : "Connect"}
                    </a>
                  ) : null}
                </li>
              ))}
              {/* Other tools an admin added through MCP: each signed in to apart. */}
              {mcp.map((m) => (
                <li key={m.id} className="act-provider">
                  <div className="act-provider-copy">
                    <span className="act-provider-name">{m.name}</span>
                    {m.connected ? (
                      <span className="act-status-ready">
                        {m.auth === "none" ? "No sign-in needed" : "Signed in"}
                      </span>
                    ) : (
                      <span className="act-status-note">
                        {m.needs_reauth ? "Sign-in expired" : "Not signed in"}
                      </span>
                    )}
                  </div>
                  {m.auth === "none" ? null : m.connected ? (
                    <button type="button" className="act-text-button" onClick={() => disconnectMcp(m.id)}>
                      Disconnect
                    </button>
                  ) : (
                    <a className="button button-secondary button-sm" href={api.mcpConnectUrl(m.id)}>
                      {m.needs_reauth ? "Sign in again" : "Sign in"}
                    </a>
                  )}
                </li>
              ))}
            </ul>
          </div>

          {recent.length > 0 && (
            <div className="act-recent account-actions">
              <div className="account-actions-head">
                <h3 className="act-recent-title">Recent actions</h3>
                <p>What Handbook prepared or sent for you.</p>
              </div>
              <div className="account-actions-list">
                {recent.map((r) =>
                  r.status === "proposed" || r.status === "executing" ? (
                    <ActionCard key={r.id} card={r} onChange={updated} />
                  ) : (
                    <ActionHistoryRow key={r.id} action={r} />
                  ),
                )}
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="muted">Loading…</p>
      )}
    </section>
  );
}
