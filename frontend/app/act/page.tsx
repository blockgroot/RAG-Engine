"use client";

/**
 * Act: the person's own accounts for writing, and what they asked Handbook to do.
 *
 * Requests are made in the chat (+ then Act), so there is one place to ask,
 * with the model picker. This page is where a person connects the accounts a
 * write runs as -- their own, so a write has their rights and shows them as
 * its author -- and where their drafts and results are listed. Drafts still
 * waiting can be approved, edited or cancelled here too.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { ActionCard } from "@/components/ActionCard";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { ApiError, api, type ActCard, type ActStatus } from "@/lib/api";

const PROVIDER_LABEL: Record<string, string> = {
  linear: "Linear",
  github: "GitHub",
  notion: "Notion",
  google: "Google Drive",
  slack: "Slack",
};

/** Why a connect was refused. Each is about keeping company content inside
 *  the company: Handbook only writes into the company's own workspace. */
const CONNECT_ERROR: Record<string, string> = {
  workspace:
    "That account is in a different workspace from your company's. Connect the account you use for work.",
  account: "That Google account isn't your work account. Connect the one you sign in to Handbook with.",
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
  const provider = PROVIDER_LABEL[action.provider ?? ""] ?? action.provider ?? "tool";
  return (
    <article className="act-history-row">
      <div className="act-history-copy">
        <strong>{action.label}</strong>
        <span>{action.payload.title || action.target.name}</span>
      </div>
      <time dateTime={action.created_at}>
        {new Date(action.created_at).toLocaleDateString(undefined, {
          day: "numeric",
          month: "short",
          year: "numeric",
        })}
      </time>
      <div className="act-history-outcome">
        <span data-status={action.status}>{HISTORY_STATUS[action.status]}</span>
        {action.status === "succeeded" && action.result?.url ? (
          <a href={action.result.url} target="_blank" rel="noreferrer">
            Open in {provider}
          </a>
        ) : null}
      </div>
    </article>
  );
}

export default function ActPage() {
  const { me, loading } = useMe();
  const [status, setStatus] = useState<ActStatus | null>(null);
  const [recent, setRecent] = useState<ActCard[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.actStatus();
      setStatus(next);
      setRecent(next.recent ?? []);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load Act.");
    }
  }, []);

  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("connected");
    const failed = params.get("connect_error");
    if (connected) setNotice(`${PROVIDER_LABEL[connected] ?? connected} is connected. Handbook can now act for you there, after you approve.`);
    if (failed) {
      setError(CONNECT_ERROR[params.get("reason") ?? ""] ?? `Couldn’t connect ${PROVIDER_LABEL[failed] ?? failed}. Try again.`);
    }
    if (connected || failed) window.history.replaceState(null, "", "/act");
  }, []);

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading&hellip;</p>
      </main>
    );
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

  const shownProviders = (status?.providers ?? []).filter((p) => p.needed || p.connected || p.needs_reauth);

  return (
    <AppShell me={me}>
      <main className="page-wide studio-page stack act-page">
        <PageHeader
          eyebrow="Act"
          title="Your accounts and actions"
          description="The accounts Handbook writes with for you, and what you've asked it to do."
          actions={
            <Link className="button" href="/chat?mode=act">
              Open Act in chat
            </Link>
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

        {status && !status.enabled ? (
          <section className="studio-panel">
            <h2>Act isn&rsquo;t switched on</h2>
            <p className="muted">Ask your admin. Until then, Handbook only reads.</p>
          </section>
        ) : status && status.actions.length === 0 ? (
          <section className="studio-panel">
            <h2>No actions yet</h2>
            <p className="muted">
              Your admin decides what Handbook may do. Nothing is switched on yet.
            </p>
          </section>
        ) : status ? (
          <>
              <aside className="studio-panel act-accounts-panel" aria-labelledby="act-accounts">
                <div className="studio-section-head">
                  <p className="act-step-label">Your access</p>
                  <h2 id="act-accounts">Accounts</h2>
                  <p className="muted">Connect a tool before asking Handbook to write there.</p>
                </div>
                <ul className="act-providers">
                  {shownProviders.map((p) => (
                    <li key={p.provider} className="act-provider">
                      <div className="act-provider-copy">
                        <span className="act-provider-name">{PROVIDER_LABEL[p.provider] ?? p.provider}</span>
                        {p.bot ? (
                          <span className={p.connected ? "act-status-ready" : "act-status-note"}>
                            {p.connected ? "Ready through the Handbook bot" : "Use Act in Slack once to link"}
                          </span>
                        ) : !p.available && !p.connected ? (
                          <span className="act-status-note">Admin setup required</span>
                        ) : p.connected ? (
                          <span className="act-status-ready">Connected as {p.account}</span>
                        ) : (
                          <span className="act-status-note">{p.needs_reauth ? "Connection expired" : "Not connected"}</span>
                        )}
                      </div>
                      {p.connected && !p.bot ? (
                        <button type="button" className="act-text-button" onClick={() => disconnect(p.provider)}>
                          Disconnect
                        </button>
                      ) : !p.bot && p.available ? (
                        <a className="button button-secondary button-sm" href={api.actConnectUrl(p.provider)}>
                          {p.needs_reauth ? "Reconnect" : "Connect"}
                        </a>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </aside>

            {recent.length > 0 && (
              <section className="studio-panel act-recent" aria-labelledby="act-recent">
                <div className="studio-section-head">
                  <p className="act-step-label">History</p>
                  <h2 id="act-recent">Recent actions</h2>
                </div>
                <div className="act-history-list">
                  {recent
                    .map((r) => r.status === "proposed" || r.status === "executing" ? (
                      <ActionCard key={r.id} card={r} onChange={updated} />
                    ) : (
                      <ActionHistoryRow key={r.id} action={r} />
                    ))}
                </div>
              </section>
            )}
          </>
        ) : null}
      </main>
    </AppShell>
  );
}
