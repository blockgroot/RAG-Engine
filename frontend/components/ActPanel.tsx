"use client";

/**
 * Agent on the Account page: the person's own sign-in to each tool an admin
 * added (an MCP server), and what they asked Handbook to do.
 *
 * A write runs with the person's OWN sign-in, so it has their rights and shows
 * them as its author; this is where they sign in and out. Requests are made in
 * the chat (Agent mode). Drafts still waiting can be approved, edited or
 * cancelled from the history here.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { ActionCard } from "@/components/ActionCard";
import { ApiError, api, type ActCard, type ActStatus, type McpServerMine } from "@/lib/api";

/** Why a sign-in was refused: Handbook only writes into the company's own
 *  workspace, with the person's work account, so company content stays inside. */
const CONNECT_ERROR: Record<string, string> = {
  workspace:
    "That account isn't your company's workspace or your work account. Sign in with the one you use for work.",
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
  const provider = action.label.split(":")[0] || "the tool";
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
    if (connected) setNotice("Signed in. Handbook can now act for you there, after you approve each draft.");
    if (failed) setError(CONNECT_ERROR[params.get("reason") ?? ""] ?? "Couldn’t sign you in. Try again.");
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

  function updated(next: ActCard) {
    setRecent((all) => [next, ...all.filter((a) => a.id !== next.id)]);
  }

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
            Sign in to each tool Handbook may write to for you. Every draft waits for your
            approval. Ask in the chat.
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

      {status && mcp.length === 0 ? (
        <p className="muted">
          Your admin hasn&rsquo;t added any tools for Agent mode yet.
        </p>
      ) : status ? (
        <>
          <div className="account-act-accounts">
            <div className="account-act-section-head">
              <h3>Your sign-ins</h3>
              <p>Drafts are sent with your own account in each tool.</p>
            </div>
            <ul className="act-providers">
              {/* Each tool an admin added (an MCP server), signed in to apart. */}
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
                    <ActionCard
                      key={r.id}
                      card={r}
                      onChange={updated}
                      onNext={(next) => setRecent((all) => [...next.actions, ...all])}
                    />
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
