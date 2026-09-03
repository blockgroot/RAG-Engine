"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { AskHeroArt } from "@/components/AskHeroArt";
import { ChatMessageView, Message } from "@/components/ChatMessage";
import { SpacePanel } from "@/components/SpacePanel";
import { useMe } from "@/lib/useMe";
import { streamChat } from "@/lib/sse";
import { api, ModelChoice } from "@/lib/api";
import { JOB_POLL_MS } from "@/lib/jobPoll";
import {
  getCachedSuggestions,
  setCachedSuggestions,
  suggestionsCacheKey,
} from "@/lib/suggestionsCache";

/** Two overlapping heads — the same shorthand Slack uses for a channel's
 *  member list, so the button needs no explaining. */
function PeopleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="9" cy="8" r="3.25" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M3.5 19c0-2.9 2.46-5 5.5-5s5.5 2.1 5.5 5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <path
        d="M16 6.2a3 3 0 0 1 0 5.6M17.5 14.4c1.9.6 3 2.3 3 4.6"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

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


/** "Notion", "Notion and Slack", "Notion, Slack and GitHub" — a readable list
 *  rather than a template that only ever handled one or two names. */
function listCopy(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

export default function ChatPage() {
  const workspaceId = useParams<{ id?: string }>().id ?? null;
  return <ChatPageInner workspaceId={workspaceId} />;
}

function ChatPageInner({ workspaceId }: { workspaceId: string | null }) {
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

  const [models, setModels] = useState<ModelChoice[]>([]);
  // The deployment's own model name, shown on the default option instead of
  // "Auto". Falls back until /chat/models answers.
  const [defaultLabel, setDefaultLabel] = useState("Auto");
  // "auto" = send no model at all, i.e. the deployment's configured default.
  // Restored from localStorage so a preference survives a reload; a stale id
  // that has since left the catalog is discarded on load rather than sent and
  // rejected with a 400.
  const [model, setModel] = useState<string>("auto");
  const [workspaceGithub, setWorkspaceGithub] = useState(false);
  const [workspaceSlack, setWorkspaceSlack] = useState(false);
  const [workspaceLinear, setWorkspaceLinear] = useState(false);
  const [workspaceNotion, setWorkspaceNotion] = useState(false);
  const [workspaceDrive, setWorkspaceDrive] = useState(false);
  const [workspacePolicyReady, setWorkspacePolicyReady] = useState(false);
  const [workspaceName, setWorkspaceName] = useState<string | null>(null);
  const [workspaceRole, setWorkspaceRole] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const codeAvailable = workspaceId ? workspaceGithub : Boolean(me?.github_connected);
  const slackAvailable = workspaceId ? workspaceSlack : Boolean(me?.slack_ready);
  const linearAvailable = workspaceId ? workspaceLinear : Boolean(me?.linear_ready);
  const notionAvailable = workspaceId ? workspaceNotion : Boolean(me?.notion_ready);
  const driveAvailable = workspaceId ? workspaceDrive : Boolean(me?.drive_ready);
  const policiesReady = readyToAsk !== false;
  const policyReady = workspaceId ? workspacePolicyReady : Boolean(me?.policy_ready);
  const policyFallbackAvailable = !notionAvailable && !driveAvailable && policyReady;
  const connectedSourceCount = [
    policyFallbackAvailable,
    codeAvailable,
    slackAvailable,
    linearAvailable,
    notionAvailable,
    driveAvailable,
  ].filter(Boolean).length;
  const anySourceAvailable = connectedSourceCount > 0;


  useEffect(() => {
    let cancelled = false;
    api
      .chatModels()
      .then(({ models: available, default_label }) => {
        if (cancelled) return;
        setModels(available);
        if (default_label) setDefaultLabel(default_label);
        const saved = localStorage.getItem("chat.model");
        // Only restore a choice the backend still offers.
        if (saved && available.some((m) => m.id === saved)) setModel(saved);
      })
      // A picker is an enhancement: if the catalog cannot be read, chat still
      // works on the default model. Never surface this as a chat error.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!me) return;
    let cancelled = false;
    const cacheKey = suggestionsCacheKey(workspaceId);
    const cached = getCachedSuggestions(cacheKey);
    if (cached) {
      setSuggestedQuestions(cached);
      setSuggestionsLoading(false);
    } else {
      setSuggestionsLoading(true);
    }
    api
      .chatSuggestions(workspaceId)
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
  }, [me, workspaceId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: busy ? "auto" : "smooth",
      block: "end",
    });
  }, [messages, busy]);

  useEffect(() => {
    if (!me) return;
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
        setWorkspaceSlack(Boolean(ws.slack_ready));
        setWorkspaceLinear(Boolean(ws.linear_ready));
        setWorkspaceNotion(Boolean(ws.notion_ready));
        setWorkspaceDrive(Boolean(ws.drive_ready));
        setWorkspacePolicyReady(Boolean(ws.policy_ready));
        setWorkspaceName(ws.name);
        setWorkspaceRole(ws.role);
      })
      .catch(() => {
        if (!cancelled) setReadyToAsk(false);
      });
    return () => {
      cancelled = true;
    };
  }, [me, workspaceId]);

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
    }
    return conversationId.current;
  }

  async function ask(question: string) {
    if (!question || busy) return;
    setInput("");
    setBusy(true);

    setMessages((prev) => [...prev, { role: "user", text: question }]);
    setMessages((prev) => [...prev, { role: "assistant", text: "", streaming: true }]);

    // Always create a conversation now. Which agent answers is decided by the
    // BACKEND, per question, so the client cannot know in advance whether this
    // one goes to GitHub (which keeps no memory and simply ignores the id).
    // Guessing wrong the other way would silently drop follow-up context.
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
            next[next.length - 1] = { role: "assistant", text: message };
            return next;
          });
          setBusy(false);
        },
      },
      workspaceId,
      // No agent pinned: the backend measures which source fits the question.
      undefined,
      model
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

  if (readyToAsk === false && !anySourceAvailable) {
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
                Finish connecting your documents in setup. We’ll bring you here automatically when
                they’re ready.
              </p>
            ) : (
              <p className="muted">
                An admin still needs to connect your company’s documents. This page updates on its own.
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

  // One box, so one set of copy. It says what the reader gets, not how the
  // product works: which source answered is shown on the answer itself, and
  // explaining the routing here read like release notes rather than an
  // invitation to type.
  const connectedNames = [
    notionAvailable && "Notion",
    driveAvailable && "Drive",
    slackAvailable && "Slack",
    linearAvailable && "Linear",
    codeAvailable && "GitHub",
  ].filter(Boolean) as string[];

  const emptyTitle = workspaceId ? "Ask this space" : "Ask your company";
  const emptyCopy =
    connectedNames.length > 0
      ? `Answers are drawn from ${listCopy(connectedNames)}. Ask for a chart when you want a count — for example, task completion by team.`
      : workspaceId
        ? "Answers are drawn from the documents connected to this space. You can also ask for a chart of what this space has recorded."
        : "Leave, benefits, remote work and more — answered from your connected documents. Ask for a chart when you want a count.";
  const composerPlaceholder = "Ask a question, or ask for a chart…";

  return (
    <AppShell me={me} variant="app">
      <div className="chat-page">
        {justSynced && (
          <div className="banner banner-ok" style={{ margin: "0 0 1rem" }}>
            {workspaceId
              ? "You’re all set — this space is ready for questions."
              : "You’re all set — your documents are ready for questions."}
          </div>
        )}

        <div className="chat-topbar">
          <div className="chat-topbar-copy">
            {/* The space name OPENS the details panel rather than navigating
                away — the Slack pattern, where a channel's people and settings
                sit behind its name and the conversation stays put. Plain text
                company-wide, which has no such panel. */}
            <p className="chat-kicker">
              {workspaceId ? (
                <button
                  type="button"
                  className="chat-kicker-link"
                  onClick={() => setPanelOpen(true)}
                >
                  {workspaceName || "Space"}
                </button>
              ) : (
                "Company-wide"
              )}
            </p>
            {/* One destination, so one title. Which source answered is stated
                per ANSWER (see ChatMessageView) rather than per page: it is a
                property of the reply, not of the box you typed into. */}
            <h1>Ask</h1>
          </div>
          {workspaceId && (
            <button
              type="button"
              className="chat-people-button"
              onClick={() => setPanelOpen(true)}
              aria-label="People in this space"
              title="People in this space"
            >
              <PeopleIcon />
              <span>People</span>
            </button>
          )}
        </div>

        {panelOpen && workspaceId && (
          <SpacePanel
            workspaceId={workspaceId}
            spaceName={workspaceName}
            isOwner={workspaceRole === "owner"}
            currentUserEmail={me?.email}
            onClose={() => setPanelOpen(false)}
          />
        )}

        <div id="ask-panel" role="tabpanel" aria-label="Ask answers">
          {messages.length === 0 ? (
            <div className="chat-empty">
              <AskHeroArt
                variant={workspaceId ? "space" : "policy"}
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
                      <ChipIcon kind="policy" />
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
          {models.length > 0 && (
            <>
              <label className="sr-only" htmlFor="model-select">
                Model
              </label>
              <span className="composer-model-wrap">
              <select
                id="model-select"
                className="composer-model"
                value={model}
                onChange={(e) => {
                  setModel(e.target.value);
                  localStorage.setItem("chat.model", e.target.value);
                }}
                disabled={busy}
                title={
                  models.find((m) => m.id === model)?.note ??
                  `Answer with the default model (${defaultLabel})`
                }
              >
                <option value="auto">{defaultLabel}</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
              </span>
            </>
          )}
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
