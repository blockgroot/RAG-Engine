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

// The GitHub agent answers from live API reads, so its examples are shaped
// around what a single bounded read can actually ground: one repo, or one
// commit. Deliberately no cross-repo question ("which service does X?") --
// nothing is embedded, so there is no semantic search across repositories.
const CODE_SUGGESTED_QUESTIONS = [
  "What does the payments service do?",
  "What happened in commit abc1234?",
  "What changed recently in the API repo?",
  "How do I run this project locally?",
];

type AgentTab = "policy" | "github";

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

  // Which agent answers. The tab only appears when GitHub is actually
  // connected -- offering "Code" with nothing behind it would just produce
  // fallbacks. Read from /me (not /admin/connections) so ordinary members see
  // it too; they can ask repo questions, they just can't manage the connection.
  // A workspace never shows it: a sub-workspace answers from its own connected
  // content only, and GitHub is org-level (see the plan's non-goals).
  const [agentTab, setAgentTab] = useState<AgentTab>("policy");
  const showAgentTabs = !workspaceId && Boolean(me?.github_connected);
  // Policies need a successful ingest before they can answer; GitHub does not,
  // because it is read live. So an org with only GitHub connected must not be
  // held behind the policy readiness gate.
  const policiesReady = readyToAsk !== false;
  const askingCode = showAgentTabs && (agentTab === "github" || !policiesReady);

  useEffect(() => {
    if (showAgentTabs && !policiesReady) setAgentTab("github");
  }, [showAgentTabs, policiesReady]);

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

    // GitHub answers are standalone: the agent has no conversation memory, and
    // POST /chat/conversations rejects agent="github" rather than handing back
    // an id that would silently do nothing. So skip the conversation entirely.
    const convId = askingCode ? null : await ensureConversation();

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
      workspaceId,
      askingCode ? "github" : "policy"
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

  // GitHub answers need no ingest, so only block when there is genuinely
  // nothing this user could ask about yet.
  if (readyToAsk === false && !showAgentTabs) {
    const syncing = workspaceId ? workspaceSyncing : me.sync_in_progress;
    return (
      <AppShell me={me} variant="app">
        <main className="page">
          <div className="card stack waiting-card">
            <p className="eyebrow">Not ready yet</p>
            <h1>
              {workspaceId
                ? "This space isn’t ready yet"
                : "Your company isn’t ready yet"}
            </h1>
            {workspaceId ? (
              <p className="muted">
                The owner still needs to connect documents and finish the first refresh. This page
                updates on its own.
              </p>
            ) : me.role === "admin" ? (
              <p className="muted">
                Finish connecting your policies in setup. We’ll bring you here automatically when
                they’re ready.
              </p>
            ) : (
              <p className="muted">
                An admin still needs to connect your company policies. This page updates on its own.
              </p>
            )}
            <div className="pulse-dot" aria-hidden />
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              {syncing
                ? "Refreshing documents — Ask unlocks when they’re ready…"
                : "Waiting for the first refresh to finish…"}
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
              ? "You’re all set — this space is ready for questions."
              : "You’re all set — company policies are ready for questions."}
          </div>
        )}
        {showAgentTabs && (
          <div className="agent-tabs" role="tablist" aria-label="What to ask about">
            <button
              type="button"
              role="tab"
              aria-selected={!askingCode}
              className={`agent-tab${!askingCode ? " is-active" : ""}`}
              onClick={() => setAgentTab("policy")}
              disabled={busy || !policiesReady}
              title={
                policiesReady ? undefined : "Company policies are still being prepared."
              }
            >
              Policies
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={askingCode}
              className={`agent-tab${askingCode ? " is-active" : ""}`}
              onClick={() => setAgentTab("github")}
              disabled={busy}
            >
              Code
            </button>
          </div>
        )}
        {messages.length === 0 ? (
          <div className="chat-empty">
            <h1>Ask a question</h1>
            <p className="muted">
              {workspaceId
                ? "Ask about the notes and docs connected to this space."
                : askingCode
                  ? "Ask about a repository or a specific commit — answers are read live from GitHub, so they’re always current."
                  : "Ask about leave, benefits, remote work, and more — answers come from your company policies."}
            </p>
            {!workspaceId && (
              <div className="suggested-chips">
                {(askingCode ? CODE_SUGGESTED_QUESTIONS : POLICY_SUGGESTED_QUESTIONS).map((q) => (
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
              workspaceId
                ? "Ask something about this space…"
                : askingCode
                  ? "Ask about a repository or a commit…"
                  : "Ask about leave, benefits, remote work…"
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
