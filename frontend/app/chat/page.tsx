"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { ChatMessageView, Message } from "@/components/ChatMessage";
import { useMe } from "@/lib/useMe";
import { streamChat } from "@/lib/sse";
import { api } from "@/lib/api";
import { JOB_POLL_MS } from "@/lib/jobPoll";

const POLICY_SUGGESTED_QUESTIONS = [
  "How many days of paid leave do I get?",
  "What's the remote work policy?",
  "How do I claim a medical reimbursement?",
  "What are the maternity/paternity leave rules?",
];

export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <main className="page">
          <p className="muted">Loading…</p>
        </main>
      }
    >
      <ChatPageInner />
    </Suspense>
  );
}

function ChatPageInner() {
  // Workspace-within-a-Workspace: ?workspace=<id> scopes the whole page to a
  // sub-workspace instead of the org-wide space -- same component, same
  // /chat/stream call, just an extra id threaded through (per the plan:
  // "the SAME chat component as the main org chat, parameterized by
  // workspace_id", not a forked second chat UI).
  const searchParams = useSearchParams();
  const workspaceId = searchParams.get("workspace");

  const { me, loading, refresh } = useMe({ enforceSetupFlow: !workspaceId });
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const conversationId = useRef<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const [readyToAsk, setReadyToAsk] = useState<boolean | null>(null);
  const [workspaceSyncing, setWorkspaceSyncing] = useState(false);
  const [justSynced, setJustSynced] = useState(false);

  useEffect(() => {
    if (!me) return;
    // Org Ask uses /me.ready_to_ask. Workspace Ask uses GET /workspaces/{id}
    // — same gate shape, scoped to that workspace's own sync (independent of
    // org-wide readiness).
    if (!workspaceId) {
      setReadyToAsk(me.ready_to_ask);
      return;
    }
    let cancelled = false;
    api
      .getWorkspace(workspaceId)
      .then((ws) => {
        if (cancelled) return;
        setWorkspaceSyncing(ws.sync_in_progress);
        setReadyToAsk(ws.ready_to_ask);
      })
      .catch(() => {
        if (!cancelled) setReadyToAsk(false);
      });
    return () => {
      cancelled = true;
    };
  }, [me, workspaceId]);

  // Poll readiness only while waiting for first sync — pause when tab is hidden.
  useEffect(() => {
    if (readyToAsk !== false) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      if (cancelled) return;
      if (typeof document !== "undefined" && document.hidden) {
        timer = setTimeout(tick, JOB_POLL_MS);
        return;
      }
      if (workspaceId) {
        const fresh = await api.getWorkspace(workspaceId).catch(() => null);
        if (cancelled || !fresh) {
          timer = setTimeout(tick, JOB_POLL_MS * 2);
          return;
        }
        setWorkspaceSyncing(fresh.sync_in_progress);
        if (fresh.ready_to_ask) {
          setReadyToAsk(true);
          setJustSynced(true);
          return;
        }
      } else {
        const fresh = await api.me().catch(() => null);
        if (cancelled || !fresh) {
          timer = setTimeout(tick, JOB_POLL_MS * 2);
          return;
        }
        if (fresh.ready_to_ask) {
          setReadyToAsk(true);
          setJustSynced(true);
          refresh();
          return;
        }
      }
      timer = setTimeout(tick, JOB_POLL_MS);
    }

    function onVisibility() {
      if (cancelled || document.hidden) return;
      if (timer) clearTimeout(timer);
      void tick();
    }

    void tick();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [readyToAsk, refresh, workspaceId]);

  async function ensureConversation() {
    if (conversationId.current) return conversationId.current;
    try {
      const { conversation_id } = await api.createConversation(workspaceId);
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

    await streamChat(
      question,
      convId,
      {
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
      },
      workspaceId
    );

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
    const syncing = workspaceId ? workspaceSyncing : me.sync_in_progress;
    return (
      <AppShell me={me} variant="app">
        <main className="page">
          <div className="card stack waiting-card">
            <p className="eyebrow">Not ready yet</p>
            <h1>
              {workspaceId
                ? "This workspace is still setting up"
                : "Your organization is still setting up"}
            </h1>
            {workspaceId ? (
              <p className="muted">
                The workspace owner needs to connect a source and finish syncing before you can ask
                questions here. This page updates automatically — no refresh needed.
              </p>
            ) : me.role === "admin" ? (
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
              {syncing
                ? workspaceId
                  ? "Sync in progress — Ask unlocks when this workspace's content is ingested…"
                  : "Sync in progress — Ask unlocks when every policy page is ingested…"
                : workspaceId
                  ? "Waiting for a completed workspace sync…"
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
            {workspaceId
              ? "Sync complete — this workspace is ready. You can ask questions now."
              : "Sync complete — all policies are ready. You can ask questions now."}
          </div>
        )}
        {messages.length === 0 ? (
          <div className="chat-empty">
            <h1>Ask a question</h1>
            <p className="muted">
              {workspaceId
                ? "Ask anything about the content connected to this workspace."
                : "Ask anything about your company’s policies — leave, benefits, remote work, and more."}
            </p>
            {!workspaceId && (
              <div className="suggested-chips">
                {POLICY_SUGGESTED_QUESTIONS.map((q) => (
                  <button key={q} type="button" className="suggested-chip" onClick={() => ask(q)}>
                    {q}
                  </button>
                ))}
              </div>
            )}
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
            placeholder={
              workspaceId ? "Ask a question about this workspace…" : "Ask about leave, benefits, remote work…"
            }
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
