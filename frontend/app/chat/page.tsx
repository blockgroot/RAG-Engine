"use client";

import { useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ChatMessageView, Message } from "@/components/ChatMessage";
import { useMe } from "@/lib/useMe";
import { streamChat } from "@/lib/sse";
import { api } from "@/lib/api";

const SYNC_POLL_MS = 4000;

const SUGGESTED_QUESTIONS = [
  "How many days of paid leave do I get?",
  "What's the remote work policy?",
  "How do I claim a medical reimbursement?",
  "What are the maternity/paternity leave rules?",
];

export default function ChatPage() {
  const { me, loading, refresh } = useMe();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const conversationId = useRef<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const [readyToAsk, setReadyToAsk] = useState<boolean | null>(null);
  const [justSynced, setJustSynced] = useState(false);

  useEffect(() => {
    if (!me) return;
    setReadyToAsk(me.ready_to_ask);
  }, [me]);

  useEffect(() => {
    if (readyToAsk !== false) return;
    let cancelled = false;
    const interval = setInterval(async () => {
      const fresh = await api.me().catch(() => null);
      if (cancelled || !fresh) return;
      if (fresh.ready_to_ask) {
        setReadyToAsk(true);
        setJustSynced(true);
        refresh();
      }
    }, SYNC_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [readyToAsk, refresh]);

  async function ensureConversation() {
    if (conversationId.current) return conversationId.current;
    try {
      const { conversation_id } = await api.createConversation();
      conversationId.current = conversation_id;
    } catch {
      // Memory may be disabled server-side; each question just goes standalone.
    }
    return conversationId.current;
  }

  async function ask(question: string) {
    if (!question || busy) return;
    setInput("");
    setBusy(true);

    setMessages((prev) => [...prev, { role: "user", text: question }]);
    setMessages((prev) => [...prev, { role: "assistant", text: "", streaming: true }]);

    const convId = await ensureConversation();

    await streamChat(question, convId, {
      onToken: (chunk) => {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, text: last.text + chunk };
          return next;
        });
      },
      onDone: (payload) => {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, streaming: false, done: payload };
          return next;
        });
        setBusy(false);
      },
      onError: (message) => {
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = { role: "assistant", text: `Error: ${message}` };
          return next;
        });
        setBusy(false);
      },
    });

    requestAnimationFrame(() => {
      logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
    });
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    ask(input.trim());
  }

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  if (readyToAsk === false) {
    return (
      <AppShell me={me} variant="app">
        <main className="page">
          <div className="card stack waiting-card">
            <p className="eyebrow">Not ready yet</p>
            <h1>Your organization is still setting up</h1>
            {me.role === "admin" ? (
              <p className="muted">
                Finish connecting a policy source and syncing in setup. You&rsquo;ll be redirected
                automatically when documents are ready.
              </p>
            ) : (
              <p className="muted">
                An admin needs to connect your company&rsquo;s policy documents before you can ask
                questions. This page updates automatically — no refresh needed.
              </p>
            )}
            <div className="pulse-dot" aria-hidden />
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              {me.sync_in_progress
                ? "Sync in progress — Ask unlocks when every policy page is ingested…"
                : "Waiting for a completed policy sync…"}
            </p>
          </div>
        </main>
      </AppShell>
    );
  }

  return (
    <AppShell me={me} variant="app">
      <div className="chat-page">
        {justSynced && (
          <div className="banner banner-ok" style={{ margin: "0 0 1rem" }}>
            Sync complete — all policies are ready. You can ask questions now.
          </div>
        )}
        {messages.length === 0 ? (
          <div className="chat-empty">
            <h1>Ask a question</h1>
            <p className="muted">
              Ask anything about your company&rsquo;s policies — leave, benefits, remote work, and
              more.
            </p>
            <div className="suggested-chips">
              {SUGGESTED_QUESTIONS.map((q) => (
                <button key={q} type="button" className="suggested-chip" onClick={() => ask(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="chat-log" ref={logRef}>
            {messages.map((m, i) => (
              <ChatMessageView key={i} message={m} />
            ))}
          </div>
        )}
        <form onSubmit={handleSubmit} className="chat-composer">
          <input
            className="chat-composer-input"
            placeholder="Ask about leave, benefits, remote work…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy}
            autoFocus
          />
          <button
            className="chat-composer-send"
            type="submit"
            disabled={busy || !input.trim()}
            aria-label="Send"
          >
            {busy ? "…" : "↑"}
          </button>
        </form>
      </div>
    </AppShell>
  );
}
