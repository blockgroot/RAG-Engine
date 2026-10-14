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

const PROVIDER_LABEL: Record<string, string> = { linear: "Linear", github: "GitHub" };

const CONNECT_ERROR: Record<string, string> = {
  workspace: "That Linear account is in a different Linear workspace from your company's.",
};

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
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Act"
          title="Ask Handbook to do it"
          description="File an issue or leave a comment in your tools. Handbook drafts it, you check it, and it’s sent as you only when you approve."
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
            <section className="studio-panel" aria-labelledby="act-accounts">
              <div className="studio-section-head">
                <h2 id="act-accounts">Your accounts</h2>
                <p className="muted">
                  Handbook acts with your own account, so it can only do what you can, and
                  your name is on what it sends. Disconnect any time.
                </p>
              </div>
              <ul className="act-providers">
                {shownProviders.map((p) => (
                  <li key={p.provider} className="act-provider">
                    <span className="act-provider-name">{PROVIDER_LABEL[p.provider] ?? p.provider}</span>
                    {p.connected ? (
                      <>
                        <span className="studio-chip studio-chip-ok">Connected as {p.account}</span>
                        <button type="button" className="button button-secondary" onClick={() => disconnect(p.provider)}>
                          Disconnect
                        </button>
                      </>
                    ) : (
                      <a className="button" href={api.actConnectUrl(p.provider)}>
                        {p.needs_reauth ? "Reconnect" : "Connect"}
                      </a>
                    )}
                  </li>
                ))}
              </ul>
              <p className="muted act-can-do">
                You can ask me to: {status.actions.map((a) => a.label).join(" · ")}.
              </p>
            </section>

            <section className="studio-panel" aria-labelledby="act-ask">
              <form onSubmit={submit} className="stack">
                <div className="field">
                  <label id="act-ask" htmlFor="act-question">
                    What should Handbook do?
                  </label>
                  <textarea
                    id="act-question"
                    className="input act-input"
                    rows={3}
                    maxLength={4000}
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    placeholder="File a Linear issue in Platform about the login timeout from yesterday’s #eng thread"
                  />
                </div>
                <div>
                  <button type="submit" className="button" disabled={busy || !question.trim()}>
                    {busy ? "Drafting…" : "Draft it"}
                  </button>
                </div>
              </form>
              {reply && <p className="act-reply">{reply}</p>}
              {card && <ActionCard card={card} onChange={updated} />}
            </section>

            {recent.filter((r) => r.id !== card?.id).length > 0 && (
              <section className="studio-panel" aria-labelledby="act-recent">
                <div className="studio-section-head">
                  <h2 id="act-recent">Recent</h2>
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
