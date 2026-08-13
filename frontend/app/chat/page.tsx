"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { AskHeroArt } from "@/components/AskHeroArt";
import { ChatMessageView, Message } from "@/components/ChatMessage";
import { useMe } from "@/lib/useMe";
import { streamChat } from "@/lib/sse";
import { api } from "@/lib/api";
import { JOB_POLL_MS } from "@/lib/jobPoll";
import {
  getCachedSuggestions,
  setCachedSuggestions,
  suggestionsCacheKey,
} from "@/lib/suggestionsCache";

function ChipIcon({ kind }: { kind: "policy" | "code" }) {
  if (kind === "code") {
    return (
      <svg className="suggested-chip-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path d="M8 8 4 12l4 4M16 8l4 4-4 4M14 6l-4 12" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <svg className="suggested-chip-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M7 4h10a2 2 0 0 1 2 2v14l-7-3-7 3V6a2 2 0 0 1 2-2Z" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
    </svg>
  );
}

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
  const bottomRef = useRef<HTMLDivElement>(null);

  const [readyToAsk, setReadyToAsk] = useState<boolean | null>(null);
  const [workspaceSyncing, setWorkspaceSyncing] = useState(false);
  const [justSynced, setJustSynced] = useState(false);
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);


  // Which agent answers. The tab only appears when GitHub is actually
  // connected -- offering "Code" with nothing behind it would just produce
  // fallbacks.
  //
  // Two independent signals, deliberately not merged: org-wide chat reads
  // /me.github_connected (available to every member, unlike admin-only
  // /admin/connections), while a workspace reads that WORKSPACE's own
  // github_connected from GET /workspaces/{id}. An org-wide connection must not
  // light up a workspace's Code tab -- the workspace answers only from its own
  // installation, so offering the tab would promise code it cannot read.
  const [agentTab, setAgentTab] = useState<AgentTab>("policy");
  const [workspaceGithub, setWorkspaceGithub] = useState(false);
  const showAgentTabs = workspaceId
    ? workspaceGithub
    : Boolean(me?.github_connected);
  // Policies need a successful ingest before they can answer; GitHub does not,
  // because it is read live. So an org with only GitHub connected must not be
  // held behind the policy readiness gate.
  const policiesReady = readyToAsk !== false;
  const askingCode = showAgentTabs && (agentTab === "github" || !policiesReady);

  // Policies and Code are separate surfaces (different agents, different
  // starters). Switching tabs must open that tab's empty template — not leave
  // the other tab's thread on screen with only the placeholder changed.
  function switchAgentTab(next: AgentTab) {
    if (next === agentTab || busy) return;
    setAgentTab(next);
    setMessages([]);
    setInput("");
    conversationId.current = null;
  }

  useEffect(() => {
    if (showAgentTabs && !policiesReady) setAgentTab("github");
  }, [showAgentTabs, policiesReady]);

  // Starter chips from connected sources (document titles / GitHub repos).
  useEffect(() => {
    if (!me) return;
    let cancelled = false;
    const agent = askingCode ? "github" : "policy";
    const cacheKey = suggestionsCacheKey(agent, workspaceId);
    const cached = getCachedSuggestions(cacheKey);
    if (cached) {
      // Seed from cache so switching back to an already-fetched tab/workspace
      // renders instantly instead of flashing "Loading suggestions…" again.
      setSuggestedQuestions(cached);
      setSuggestionsLoading(false);
    } else {
      setSuggestionsLoading(true);
    }
    api
      .chatSuggestions(agent, workspaceId)
      .then((res) => {
        const questions = res.questions || [];
        setCachedSuggestions(cacheKey, questions);
        if (!cancelled) setSuggestedQuestions(questions);
      })
      .catch(() => {
        if (!cancelled && !cached) setSuggestedQuestions([]);
      })
      .finally(() => {
        if (!cancelled) setSuggestionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [me, askingCode, workspaceId]);

  // Keep the latest turn in view on send, while tokens stream, and when done.
  // scrollIntoView walks scrollable ancestors (chat-log and/or app-body); the
  // old one-shot logRef.scrollTo after streamChat often no-oped when the outer
  // shell was the real scroller, and never followed streaming tokens.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: busy ? "auto" : "smooth",
      block: "end",
    });
  }, [messages, busy]);

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
        setWorkspaceGithub(Boolean(ws.github_connected));
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
            next[next.length - 1] = { role: "assistant", text: message };
            return next;
          });
          setBusy(false);
        },
      },
      workspaceId,
      askingCode ? "github" : "policy"
    );

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

  // Code copy takes precedence over the workspace copy: a workspace can now have
  // its own GitHub connection, so "this space" wording would misdescribe what is
  // actually being asked.
  const emptyTitle = askingCode
    ? workspaceId
      ? "Ask this space’s code"
      : "Ask your code"
    : workspaceId
      ? "Ask this space"
      : "Ask your company";
  const emptyCopy = askingCode
    ? "Repository and commit answers are read live from GitHub — always current, never stale."
    : workspaceId
      ? "Answers come only from the notes and docs connected to this space."
      : "Leave, benefits, remote work, and more — grounded in your connected policies.";
  const composerPlaceholder = askingCode
    ? "Ask about a repository or a commit…"
    : workspaceId
      ? "Ask something about this space…"
      : "Ask about leave, benefits, remote work…";

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

        <div className="chat-topbar">
          <div className="chat-topbar-copy">
            <p className="chat-kicker">{workspaceId ? "Space" : "Workspace"}</p>
            <h1>{askingCode ? "Code" : workspaceId ? "Space Ask" : "Ask"}</h1>
          </div>
          {showAgentTabs && (
            <div className="agent-tabs" role="tablist" aria-label="What to ask about">
              <button
                type="button"
                role="tab"
                id="tab-policies"
                aria-controls="ask-panel"
                aria-selected={!askingCode}
                className={`agent-tab${!askingCode ? " is-active" : ""}`}
                onClick={() => switchAgentTab("policy")}
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
                id="tab-code"
                aria-controls="ask-panel"
                aria-selected={askingCode}
                className={`agent-tab${askingCode ? " is-active" : ""}`}
                onClick={() => switchAgentTab("github")}
                disabled={busy}
              >
                Code
              </button>
            </div>
          )}
        </div>

        <div id="ask-panel" role="tabpanel" aria-label="Ask answers">
          {messages.length === 0 ? (
            <div className="chat-empty">
              <AskHeroArt
                variant={workspaceId ? "space" : askingCode ? "code" : "policy"}
              />
              <div className="chat-empty-copy">
                <h1>{emptyTitle}</h1>
                <p className="muted">{emptyCopy}</p>
              </div>
              {suggestionsLoading ? (
                <p className="muted suggested-loading">Loading suggestions…</p>
              ) : suggestedQuestions.length > 0 ? (
                <div className="suggested-chips suggested-chips-bento">
                  {suggestedQuestions.map((q) => (
                    <button
                      key={q}
                      type="button"
                      className="suggested-chip suggested-chip-card"
                      onClick={() => ask(q)}
                    >
                      <ChipIcon kind={askingCode ? "code" : "policy"} />
                      <span>{q}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="chat-log" ref={logRef} aria-live="polite">
              {messages.map((m, i) => (
                <ChatMessageView key={i} message={m} />
              ))}
              <div ref={bottomRef} aria-hidden className="chat-scroll-anchor" />
            </div>
          )}
        </div>

        <form onSubmit={handleSubmit} className="chat-composer" aria-label="Ask a question">
          <label className="sr-only" htmlFor="ask-input">
            Your question
          </label>
          <input
            id="ask-input"
            className="chat-composer-input"
            placeholder={composerPlaceholder}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy}
            autoFocus
          />
          <button
            className="chat-composer-send"
            type="submit"
            disabled={busy || !input.trim()}
            aria-label="Send question"
          >
            {busy ? (
              <span className="composer-spinner" aria-hidden />
            ) : (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path
                  d="M12 19V5M12 5l-6 6M12 5l6 6"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            )}
          </button>
        </form>
      </div>
    </AppShell>
  );
}
