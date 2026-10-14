"use client";

/**
 * Act: ask Handbook to DO something in a connected tool.
 *
 * A separate page from Ask on purpose. Ask only reads; here every request
 * ends in a card the person approves, and nothing is sent before that. Each
 * person connects their own account, so a write runs with their rights and
 * shows them as its author. The page says all of that in plain words,
 * because a person who thinks "the bot did it" reviews a draft less carefully
 * than one who knows it goes out under their name.
 */

import { useCallback, useEffect, useState } from "react";

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

function CheckIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" fill="none">
      <path d="m5 10 3 3 7-7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function ActPage() {
  const { me, loading } = useMe();
  const [status, setStatus] = useState<ActStatus | null>(null);
  const [question, setQuestion] = useState("");
  const [reply, setReply] = useState<string | null>(null);
  const [card, setCard] = useState<ActCard | null>(null);
  const [recent, setRecent] = useState<ActCard[]>([]);
  const [busy, setBusy] = useState(false);
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

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    setBusy(true);
    setError(null);
    setReply(null);
    setCard(null);
    try {
      const out = await api.act(question.trim());
      setReply(out.reply);
      setCard(out.action);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Nothing was sent.");
    } finally {
      setBusy(false);
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
    setCard((c) => (c && c.id === next.id ? next : c));
    setRecent((all) => [next, ...all.filter((a) => a.id !== next.id)]);
  }

  const shownProviders = (status?.providers ?? []).filter((p) => p.needed || p.connected || p.needs_reauth);

  return (
    <AppShell me={me}>
      <main className="page-wide studio-page stack act-page">
        <PageHeader
          eyebrow="Act"
          title="Turn a request into action"
          description="Tell Handbook what to do and where. You’ll review the exact draft before anything is sent."
        />

        <ol className="act-steps" aria-label="How Act works">
          <li>
            <span>1</span>
            <div><strong>Describe it</strong><small>Name the tool and target</small></div>
          </li>
          <li>
            <span>2</span>
            <div><strong>Review it</strong><small>Check the exact draft</small></div>
          </li>
          <li>
            <span>3</span>
            <div><strong>Approve it</strong><small>Nothing sends before this</small></div>
          </li>
        </ol>

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
            <div className="act-workspace">
              <section className="studio-panel act-compose-panel" aria-labelledby="act-ask">
                <div className="act-compose-heading">
                  <p className="act-step-label">Your request</p>
                  <h2 id="act-ask">What should Handbook do?</h2>
                  <p className="muted">
                    Name the tool and the exact team, repo, page, or channel. Handbook will never choose a destination for you.
                  </p>
                </div>
                <form onSubmit={submit} className="act-form">
                  <label className="sr-only" htmlFor="act-question">Describe the action</label>
                  <textarea
                    id="act-question"
                    className="input act-input"
                    rows={5}
                    maxLength={4000}
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    placeholder={'For example: Create a Linear issue in Platform called "Fix scheduler retries" using yesterday’s #eng discussion.'}
                  />
                  <div className="act-form-footer">
                    <p><CheckIcon /> You&rsquo;ll review everything before it&rsquo;s sent.</p>
                    <button type="submit" className="button act-draft-button" disabled={busy || !question.trim()}>
                      {busy ? "Preparing draft…" : "Review draft"}
                    </button>
                  </div>
                </form>
                {reply && <p className="act-reply">{reply}</p>}
                {card && <ActionCard card={card} onChange={updated} />}
              </section>

              <aside className="studio-panel act-accounts-panel" aria-labelledby="act-accounts">
                <div className="studio-section-head">
                  <p className="act-step-label">Before you start</p>
                  <h2 id="act-accounts">Connected accounts</h2>
                  <p className="muted">Actions use your access and appear under your identity.</p>
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
                <details className="act-capabilities">
                  <summary>What can Handbook do?</summary>
                  <ul>
                    {status.actions.map((action) => <li key={action.name}>{action.label}</li>)}
                  </ul>
                </details>
              </aside>
            </div>

            {recent.filter((r) => r.id !== card?.id).length > 0 && (
              <section className="studio-panel act-recent" aria-labelledby="act-recent">
                <div className="studio-section-head">
                  <p className="act-step-label">History</p>
                  <h2 id="act-recent">Recent actions</h2>
                </div>
                <div className="stack">
                  {recent
                    .filter((r) => r.id !== card?.id)
                    .map((r) => (
                      <ActionCard key={r.id} card={r} onChange={updated} />
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
