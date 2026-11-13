"use client";

/**
 * Agent on the Account page: the person's own sign-in to each tool an admin
 * added (an MCP server), and what they asked Handbook to do.
 * A write runs with the person's OWN sign-in. Requests are made in chat;
 * pending drafts can be decided here.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { ActionCard } from "@/components/ActionCard";
import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { ApiError, api, type ActCard, type ActStatus, type McpServerMine } from "@/lib/api";

/** Why a sign-in was refused: Handbook only writes into the company's own
 *  workspace, with the person's work account, so company content stays inside. */
const CONNECT_ERROR: Record<string, string> = {
  workspace:
    "That account isn't your company's workspace or your work account. Sign in with the one you use for work.",
};

/** A tool's logo from its name ("Google Sheets", "GitHub", "Linear"). */
function brandFor(name: string): BrandName {
  const n = name.toLowerCase();
  if (n.includes("github")) return "github";
  if (n.includes("linear")) return "linear";
  if (n.includes("notion")) return "notion";
  if (n.includes("slack")) return "slack";
  if (n.includes("google") || n.includes("sheet") || n.includes("doc")) return "google";
  return "model";
}

/** History rows per page of the slider. */
const PAGE_SIZE = 5;

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
      <span className="account-action-mark" aria-hidden>
        <BrandGlyph name={brandFor(provider)} size={18} />
      </span>
      <div className="account-action-copy">
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
      <span className="account-action-status" data-status={action.status}>
        {HISTORY_STATUS[action.status]}
      </span>
      {action.status === "succeeded" && action.result?.url ? (
        <a
          className="account-action-open"
          href={action.result.url}
          target="_blank"
          rel="noreferrer"
          title={`Open in ${provider}`}
          aria-label={`Open in ${provider}`}
        >
          ↗
        </a>
      ) : (
        <span aria-hidden />
      )}
    </article>
  );
}

export function ActPanel() {
  const [status, setStatus] = useState<ActStatus | null>(null);
  const [mcp, setMcp] = useState<McpServerMine[]>([]);
  const [recent, setRecent] = useState<ActCard[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [page, setPage] = useState(0);

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

  // A draft still open (or sending) is a live card above the history.
  const isLive = (r: ActCard) =>
    r.status === "executing" || (r.status === "proposed" && Date.parse(r.expires_at) > Date.now());
  const live = recent.filter(isLive);
  const history = recent.filter((r) => !isLive(r));
  const pages: ActCard[][] = [];
  for (let i = 0; i < history.length; i += PAGE_SIZE) pages.push(history.slice(i, i + PAGE_SIZE));
  const shown = Math.min(page, Math.max(pages.length - 1, 0));

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
                <li key={m.id} className="act-provider" data-connected={m.connected}>
                  <span className="act-provider-mark" aria-hidden>
                    <BrandGlyph name={brandFor(m.name)} size={20} />
                  </span>
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
              {live.length > 0 && (
                <div className="account-actions-live">
                  {live.map((r) => (
                    <ActionCard
                      key={r.id}
                      card={r}
                      onChange={updated}
                      onNext={(next) => setRecent((all) => [...next.actions, ...all])}
                    />
                  ))}
                </div>
              )}
              {history.length > 0 && (
                <div className="account-actions-slider">
                  <div className="account-actions-viewport">
                    <div className="account-actions-track" style={{ transform: `translateX(-${shown * 100}%)` }}>
                      {pages.map((rows, i) => (
                        <div key={i} className="account-actions-page" aria-hidden={i !== shown}>
                          {rows.map((r) => (
                            // A draft past its expiry can no longer be approved: history.
                            <ActionHistoryRow key={r.id} action={r.status === "proposed" ? { ...r, status: "expired" } : r} />
                          ))}
                        </div>
                      ))}
                    </div>
                  </div>
                  {pages.length > 1 && (
                    <div className="account-actions-pager">
                      <button type="button" aria-label="Newer" disabled={shown === 0} onClick={() => setPage(shown - 1)}>‹</button>
                      <span className="account-actions-dots">
                        {pages.map((_, i) => (
                          <button key={i} type="button" aria-label={`Page ${i + 1}`} aria-current={i === shown} onClick={() => setPage(i)} />
                        ))}
                      </span>
                      <span className="account-actions-count">
                        {shown * PAGE_SIZE + 1}–{Math.min((shown + 1) * PAGE_SIZE, history.length)} of {history.length}
                      </span>
                      <button type="button" aria-label="Older" disabled={shown === pages.length - 1} onClick={() => setPage(shown + 1)}>›</button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      ) : (
        <p className="muted">Loading…</p>
      )}
    </section>
  );
}
