"use client";

import { ModelPicker } from "@/components/ModelPicker";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useParams, useRouter, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { ReleaseChip } from "@/components/ReleaseChip";
import { AgentIntro, AskGreeting, ChartThumb, chartKind, ModeSwitch, SOURCE_GLYPH, modelName, useTypedPlaceholder, type AskMode } from "@/components/AskHome";
import { BrandGlyph } from "@/components/BrandGlyph";
import { ActionCard } from "@/components/ActionCard";
import { ActIcon, ChartIcon, ChatMessageView, DeepIcon, Message } from "@/components/ChatMessage";
import { SpacePanel } from "@/components/SpacePanel";
import { useMe } from "@/lib/useMe";
import { streamAct, streamChat } from "@/lib/sse";
import { api, ApiError, Attachment, ModelChoice } from "@/lib/api";
import { JOB_POLL_MS } from "@/lib/jobPoll";
import { fileKind } from "@/lib/fileKind";
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

function PlusIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function ChipIcon({ kind }: { kind: "policy" | "code" | "chart" }) {
  if (kind === "chart") {
    return (
      <svg className="suggested-chip-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
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
  return (
    <Suspense fallback={null}>
      <ChatPageInner workspaceId={workspaceId} />
    </Suspense>
  );
}

// Chart starters per scope, kept for the visit: switching Chart off and on, or
// coming back to the chat, shows them at once and refreshes behind.
const chartStarterCache = new Map<string, string[]>();

function ChatPageInner({ workspaceId }: { workspaceId: string | null }) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { me, loading, refresh } = useMe({ enforceSetupFlow: !workspaceId });
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const askInputRef = useRef<HTMLTextAreaElement>(null);
  const [busy, setBusy] = useState(false);
  const conversationId = useRef<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const [readyToAsk, setReadyToAsk] = useState<boolean | null>(null);
  const [workspaceSyncing, setWorkspaceSyncing] = useState(false);
  const [justSynced, setJustSynced] = useState(false);
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([]);
  const [deepQuestions, setDeepQuestions] = useState<string[]>([]);
  const [suggestionSources, setSuggestionSources] = useState<Record<string, string>>({});
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);

  const [models, setModels] = useState<ModelChoice[]>([]);
  // The deployment's own model name, shown on the default option instead of
  // "Auto". Falls back until /chat/models answers.
  const [defaultLabel, setDefaultLabel] = useState("Auto");
  // "auto" sends no model (the deployment default). A stored id no longer in
  // the catalog is dropped on load, not sent and rejected.
  const [model, setModel] = useState<string>("auto");
  // Chart mode: the answer is a chart or a refusal. Off, charts are never guessed.
  const [chartMode, setChartMode] = useState(false);
  // Deep analysis: top documents read nearly whole for broad questions. More
  // tokens, no extra AI call. Never on with Chart.
  const [deepMode, setDeepMode] = useState(false);
  // Deep's opt-in for company-wide and list questions: reads many documents as
  // well as deeply, and answers longer. Costs more, so it is off unless chosen.
  const [extraEffort, setExtraEffort] = useState(false);
  // Agent mode (/act): replies with draft cards; nothing is sent until approved,
  // and the turn is not saved as a chat answer.
  const [actMode, setActMode] = useState(false);
  // In a space, Act also needs that space's own switch (its owner's choice,
  // within what the company allows). Company-wide, the company switch alone.
  const [spaceActOn, setSpaceActOn] = useState(true);
  // Said once when turning Chart on moved the picker off a model that cannot
  // build charts, so the switch is never silent.
  const [modelNote, setModelNote] = useState<string | null>(null);
  // Chart mode's starters come from the server: built from the metrics and
  // fields this scope actually has, so each one can be drawn.
  const [chartStarters, setChartStarters] = useState<string[] | null>(
    () => chartStarterCache.get(workspaceId ?? "") ?? null,
  );
  const [workspaceGithub, setWorkspaceGithub] = useState(false);
  const [workspaceSlack, setWorkspaceSlack] = useState(false);
  const [workspaceLinear, setWorkspaceLinear] = useState(false);
  const [workspaceNotion, setWorkspaceNotion] = useState(false);
  const [workspaceDrive, setWorkspaceDrive] = useState(false);
  const [workspacePolicyReady, setWorkspacePolicyReady] = useState(false);
  const [workspaceName, setWorkspaceName] = useState<string | null>(null);
  const [workspaceRole, setWorkspaceRole] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  // Attachments live for this conversation only; the chips explain why the
  // routing pill says "attachment".
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  // Bumped after each answer so a new chat appears in the list without a reload.
  const [historyKey, setHistoryKey] = useState(0);
  const [activeConversation, setActiveConversation] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  // True while files are dragged over the page: the whole chat is the drop target.
  const [dragging, setDragging] = useState(false);
  const attachRef = useRef<(files: FileList | null) => void>(() => undefined);
  // Names of files in flight, so the person sees WHICH are still uploading.
  const [pending, setPending] = useState<string[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
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


  // Re-read when the chat's files change: an uploaded sheet's columns
  // become the first starters.
  const attachmentKey = attachments.map((a) => a.id).join(",");
  // Fetched when the chat opens, not when Chart is clicked: the list is
  // ready by the time someone switches modes.
  useEffect(() => {
    let cancelled = false;
    const key = workspaceId ?? "";
    if (!attachmentKey) setChartStarters(chartStarterCache.get(key) ?? null);
    api
      .chartStarters(workspaceId, attachmentKey ? conversationId.current : null)
      .then((r) => {
        if (!attachmentKey) chartStarterCache.set(key, r.questions);
        if (!cancelled) setChartStarters(r.questions);
      })
      .catch(() => {
        if (!cancelled) setChartStarters((v) => v ?? []);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, attachmentKey]);

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
      setSuggestedQuestions(cached.questions);
      setDeepQuestions(cached.deep);
      setSuggestionSources(cached.sources);
      setSuggestionsLoading(false);
    } else {
      setSuggestionsLoading(true);
    }
    api
      .chatSuggestions(workspaceId)
      .then((res) => {
        const questions = res.questions || [];
        const deep = res.deep_questions || [];
        const sources = res.question_sources || {};
        setCachedSuggestions(cacheKey, { questions, deep, sources });
        if (!cancelled) {
          setSuggestedQuestions(questions);
          setDeepQuestions(deep);
          setSuggestionSources(sources);
        }
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
    const field = askInputRef.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, 160)}px`;
    field.style.overflowY = field.scrollHeight > 160 ? "auto" : "hidden";
  }, [input]);

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

  // Per SCOPE: company Ask and each space are different conversations, so one
  // key would hand a space's chat id to company Ask, where it 404s.
  const convKey = `chat.conversation.${workspaceId ?? "org"}`;

  // Open a conversation with its transcript and files, so a refresh never
  // strands uploaded files the person can no longer remove.
  const openConversation = useCallback(
    async (id: string) => {
      conversationId.current = id;
      setActiveConversation(id);
      setAttachments([]);
      setUploadError(null);
      try {
        sessionStorage.setItem(convKey, id);
      } catch {
        /* storage blocked; the chat still works */
      }
      // The FULL transcript: folding marks turns (`folded_through`), never deletes.
      const [turns, files] = await Promise.all([
        api.getConversation(id, workspaceId).then((r) => r.turns).catch(() => []),
        api.listAttachments(id, workspaceId).then((r) => r.attachments).catch(() => []),
      ]);
      setMessages(
        turns.flatMap((t) => [
          { role: "user" as const, text: t.question },
          {
            role: "assistant" as const,
            text: t.answer,
            cited: t.cited && t.cited.length ? t.cited : undefined,
            chart: t.chart?.panel ?? undefined,
            chartPeriod: t.chart?.period ?? undefined,
            meta: t.meta ?? undefined,
          },
        ]),
      );
      setAttachments(files);
    },
    [convKey, workspaceId],
  );

  // Resume this tab's last conversation. sessionStorage: survives a refresh,
  // dies with the tab.
  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = sessionStorage.getItem(convKey);
    } catch {
      // Private mode / blocked storage. Nothing to resume; not an error.
    }
    if (!saved) return;
    // Validated first: a stale id 404s and is dropped, never sent.
    api
      .getConversation(saved, workspaceId)
      .then(() => openConversation(saved as string))
      .catch(() => {
        try {
          sessionStorage.removeItem(convKey);
        } catch {
          /* nothing to clean up */
        }
      });
  }, [convKey, workspaceId, openConversation]);

  function startNewChat() {
    conversationId.current = null;
    try {
      sessionStorage.removeItem(convKey);
    } catch {
      /* nothing to clean up */
    }
    setMessages([]);
    setAttachments([]);
    setActiveConversation(null);
    setUploadError(null);
    // Drop ?c= or the param effect re-opens the chat we left. `replace`: an
    // empty chat is no back-button entry.
    if (searchParams.get("c")) router.replace(pathname);
  }

  // Handle URL query parameter: ?c={conversation_id}
  const conversationParam = searchParams.get("c");

  useEffect(() => {
    // Only open a conversation not ALREADY open: re-opening one mid-answer
    // empties the transcript and the next token throws. `conversationId.current`
    // is set before `router.replace`, so a new chat's id already matches.
    if (conversationParam && conversationParam !== conversationId.current) {
      void openConversation(conversationParam);
    }
  }, [conversationParam, openConversation]);

  // Only "new chat" is an event; opening a chat goes through the URL (`?c=`),
  // the one source of truth for which chat shows.
  useEffect(() => {
    function onNew() {
      startNewChat();
    }
    window.addEventListener("new-chat", onNew);
    return () => window.removeEventListener("new-chat", onNew);
  }, [openConversation]);

  // Notify RailChats whenever the active conversation changes
  useEffect(() => {
    window.dispatchEvent(
      new CustomEvent("active-conversation-changed", {
        detail: { id: activeConversation },
      }),
    );
  }, [activeConversation]);

  // Notify RailChats whenever chats are updated/created/deleted
  useEffect(() => {
    if (historyKey > 0) {
      window.dispatchEvent(new CustomEvent("chats-updated"));
    }
  }, [historyKey]);

  async function ensureConversation() {
    if (conversationId.current) return conversationId.current;
    try {
      const { conversation_id } = await api.createConversation(workspaceId);
      conversationId.current = conversation_id;
      setActiveConversation(conversation_id);
      // The URL says WHICH chat is open, so a new chat goes there too or a
      // refresh lands empty. `replace`: no back-button entry for the empty state.
      router.replace(`${pathname}?c=${encodeURIComponent(conversation_id)}`);
      try {
        sessionStorage.setItem(convKey, conversation_id);
      } catch {
        // Storage blocked: the chat still works, it just will not survive a
        // refresh. Never worth failing the question over.
      }
    } catch {
    }
    return conversationId.current;
  }

  attachRef.current = (files) => void attach(files);

  useEffect(() => {
    // Only a drag that carries FILES: dragging selected text or a link
    // around the page must not flash the overlay.
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    // dragenter/dragleave fire for every child crossed, so count them; the
    // overlay goes when the count returns to zero.
    let depth = 0;
    function onEnter(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth += 1;
      setDragging(true);
    }
    function onOver(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault(); // without this the browser opens the file instead
      if (e.dataTransfer) e.dataTransfer.dropEffect = busy ? "none" : "copy";
    }
    function onLeave(e: DragEvent) {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDragging(false);
    }
    function onDrop(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setDragging(false);
      if (!busy) attachRef.current(e.dataTransfer?.files ?? null);
    }
    document.addEventListener("dragenter", onEnter);
    document.addEventListener("dragover", onOver);
    document.addEventListener("dragleave", onLeave);
    document.addEventListener("drop", onDrop);
    return () => {
      document.removeEventListener("dragenter", onEnter);
      document.removeEventListener("dragover", onOver);
      document.removeEventListener("dragleave", onLeave);
      document.removeEventListener("drop", onDrop);
    };
  }, [busy]);

  async function attach(files: FileList | null) {
    if (!files?.length || uploading) return;
    setUploadError(null);
    setUploading(true);
    setPending(Array.from(files).map((f) => f.name));
    // A conversation must exist before a file can hang off it -- the row is
    // what scopes the attachment to this chat and this person.
    const convId = await ensureConversation();
    if (!convId) {
      setUploadError("Couldn't start a chat to attach that to.");
      setUploading(false);
      setPending([]);
      return;
    }
    try {
      // ONE request for the selection, so one bad file never costs the rest.
      const result = await api.uploadAttachments(convId, Array.from(files), workspaceId);
      setAttachments((prev) => [...prev, ...result.attachments]);
      if (result.rejected.length > 0) {
        // Each refusal names its own file, because "3 files failed" sends
        // someone re-uploading all of them to find out which.
        setUploadError(
          result.rejected.map((r) => r.reason).join(" "),
        );
      }
    } catch (err) {
      setUploadError(
        err instanceof ApiError ? err.message : "Couldn't upload those files.",
      );
    }
    setUploading(false);
    setPending([]);
    if (fileInput.current) fileInput.current.value = "";
  }

  async function detach(id: string) {
    const convId = conversationId.current;
    if (!convId) return;
    const removed = attachments.find((a) => a.id === id);
    if (!removed) return; // already going: a second click is not a second delete
    setUploadError(null);
    // Gone from the chat at once, as in every chat app; the server delete is
    // idempotent, so only a real failure brings the chip back.
    setAttachments((prev) => prev.filter((a) => a.id !== id));
    try {
      await api.deleteAttachment(convId, id, workspaceId);
    } catch {
      setAttachments((prev) =>
        prev.some((a) => a.id === id) ? prev : [...prev, removed],
      );
      setUploadError(`Couldn't remove ${removed.filename}. Try again.`);
    }
  }

  /** Act mode: one draft for the person to check, edit and approve. */
  async function draftAction(question: string) {
    setInput("");
    setBusy(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", text: question },
      { role: "assistant", text: "", streaming: true, steps: ["Drafting…"] },
    ]);
    let reply: Message;
    try {
      // Each step the server takes replaces the placeholder, then trails, as in Ask.
      const out = await streamAct(question, workspaceId, model, (line) =>
        setMessages((prev) => {
          if (prev.length === 0) return prev;
          const next = [...prev];
          const last = next[next.length - 1];
          const steps = last.steps?.length === 1 && last.steps[0] === "Drafting…" ? [] : (last.steps ?? []);
          next[next.length - 1] = { ...last, steps: [...steps, line] };
          return next;
        }),
      );
      // `actions` is every draft; an older server sends only `action`.
      reply = {
        role: "assistant",
        text: out.reply,
        acts: out.actions?.length ? out.actions : out.action ? [out.action] : null,
      };
    } catch (err) {
      // Said where the answer would be, like any reply (a pop-up hid it from the chat).
      const message = err instanceof Error ? err.message : "Something went wrong. Nothing was sent.";
      reply = {
        role: "assistant",
        text: message,
      };
    }
    setMessages((prev) => (prev.length ? [...prev.slice(0, -1), reply] : [reply]));
    setBusy(false);
  }

  /** `mode` overrides the composer's, for "Try Deep analysis" under an answer. */
  async function ask(question: string, mode?: "deep") {
    if (!question || busy) return;
    if (actMode && !mode) return draftAction(question);
    setInput("");
    setBusy(true);

    setMessages((prev) => [...prev, { role: "user", text: question }]);
    setMessages((prev) => [...prev, { role: "assistant", text: "", streaming: true }]);

    // Always create a conversation; the backend picks the agent per question.
    const convId = await ensureConversation();

    await streamChat(
      question,
      convId,
      {
        onStatus: (line) => {
          setMessages((prev) => {
            if (prev.length === 0) return prev;
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = { ...last, steps: [...(last.steps ?? []), line] };
            return next;
          });
        },
        onToken: (chunk) => {
          setMessages((prev) => {
            // Spreading `prev[-1]` of an empty list throws and blanks the page;
            // drop the token instead.
            if (prev.length === 0) return prev;
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = { ...last, text: last.text + chunk };
            return next;
          });
        },
        onDone: (payload) => {
          setMessages((prev) => {
            if (prev.length === 0) return prev;
            const next = [...prev];
            const last = next[next.length - 1];
            next[next.length - 1] = { ...last, streaming: false, done: payload };
            return next;
          });
          // A new chat only becomes listable once it has a turn, so the
          // list is refreshed here rather than on creation.
          setHistoryKey((k) => k + 1);
          setBusy(false);
        },
        onError: (message) => {
          setMessages((prev) => {
            if (prev.length === 0) return [{ role: "assistant", text: message }];
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
      model,
      mode ?? (chartMode ? "chart" : deepMode ? (extraEffort ? "deep_extra" : "deep") : undefined),
    );

  }

  // Slack's "Open interactive chart" (/chat?mode=chart&q=...) asks once, as the
  // clicker. Two steps so Chart mode is on before the question goes.
  useEffect(() => {
    if (!workspaceId || !me?.act_enabled) {
      setSpaceActOn(true);
      return;
    }
    let live = true;
    api
      .spaceAct(workspaceId)
      .then((s) => live && setSpaceActOn(s.enabled))
      .catch(() => live && setSpaceActOn(false));
    return () => {
      live = false;
    };
  }, [workspaceId, me?.act_enabled]);
  useEffect(() => {
    if (!spaceActOn) setActMode(false);
  }, [spaceActOn]);

  // "Open Act in chat" from the Act page: /chat?mode=act starts in Act mode.
  const actLink = searchParams.get("mode") === "act" && !searchParams.get("q");
  useEffect(() => {
    if (!actLink || !me?.act_enabled || !spaceActOn) return;
    toggleActMode(true);
    router.replace(pathname);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actLink, me]);

  const linkQuestion = searchParams.get("q");
  const [pendingAsk, setPendingAsk] = useState<string | null>(null);
  useEffect(() => {
    if (!linkQuestion || !me || conversationParam) return;
    toggleChartMode(searchParams.get("mode") === "chart");
    setPendingAsk(linkQuestion.slice(0, 500));
    router.replace(pathname);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkQuestion, me]);
  useEffect(() => {
    if (!pendingAsk || busy) return;
    const question = pendingAsk;
    setPendingAsk(null);
    void ask(question);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingAsk, busy]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    ask(input.trim());
  }

  // Before the early returns below: a hook must run on every render.
  const typedPlaceholder = useTypedPlaceholder(
    (deepMode && deepQuestions.length > 0 ? deepQuestions : suggestedQuestions).slice(0, 6),
    !actMode && !chartMode && messages.length === 0 && !input,
    "Ask a question…",
  );

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
            {workspaceId && workspaceRole === "owner" ? (
              <p className="muted">
                A new space starts empty. Connect this space’s documents and the first refresh
                starts on its own.
              </p>
            ) : workspaceId ? (
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
            {/* An owner with nothing connected is not waiting on anything — a
                progress line there reads as a stuck sync rather than a step
                they have to take. */}
            {workspaceId && workspaceRole === "owner" && !syncing ? (
              <Link className="btn" href={`/workspaces/${workspaceId}`}>
                Connect documents
              </Link>
            ) : (
              <>
                <div className="pulse-dot" aria-hidden />
                <p className="muted" style={{ fontSize: "0.85rem" }}>
                  {syncing
                    ? "Refreshing documents — Ask unlocks when they’re ready…"
                    : "Waiting for the first refresh to finish…"}
                </p>
              </>
            )}
          </div>
        </main>
      </AppShell>
    );
  }

  // One box, one set of copy: what the reader gets, not how routing works.
  const connectedNames = [
    notionAvailable && "Notion",
    driveAvailable && "Drive",
    slackAvailable && "Slack",
    linearAvailable && "Linear",
    codeAvailable && "GitHub",
  ].filter(Boolean) as string[];

  const emptyTitle = actMode
    ? "Ask Handbook to do it"
    : chartMode
    ? "Create a chart"
    : deepMode
      ? "Deep analysis"
      : workspaceId ? "Ask this space" : "Ask your company";
  const emptyCopy = actMode
    ? "File an issue, leave a comment, or post a message in your tools. Handbook drafts it, you check or edit it, and nothing is sent until you approve. It acts with your own accounts."
    : chartMode
    ? "Charts count activity in your connected apps, figures in tables inside your documents, and files you upload here (Excel, CSV, PDF or Word). Describe what you want to see."
    : deepMode
      ? extraEffort
        ? "Reads many documents, not only the closest ones. Best for a whole project or a long list. Uses more of your AI quota."
        : "Reads the most relevant documents in full. Extra effort, beside Deep, reads more of them."
      : workspaceId
        ? "Ask anything about this space. Every answer names the document it came from."
        : "Ask anything about your company. Every answer names the document it came from.";
  const starters = actMode ? [] : chartMode ? chartStarters ?? [] : deepMode && deepQuestions.length > 0 ? deepQuestions : suggestedQuestions;
  const composerPlaceholder = actMode
    ? "e.g. file a Linear issue in Platform about the login timeout"
    : chartMode
    ? "Describe a chart, e.g. issues by priority"
    : deepMode
      ? "Ask a broad question, e.g. summarise the project"
      : "Ask a question…";
  // In Chart mode only models that build charts are offered; the company's own
  // model stays, marked, because we cannot know in advance.
  const pickerModels = chartMode ? models.filter((m) => m.charts !== false) : models;
  const mode: AskMode = actMode ? "act" : chartMode ? "chart" : deepMode ? "deep" : "ask";
  const firstName = me?.name?.trim().split(/\s+/)[0] ?? null;

  function toggleChartMode(on: boolean) {
    setChartMode(on);
    if (on) {
      setDeepMode(false);
      setActMode(false);
    }
    setModelNote(null);
    const current = models.find((m) => m.id === model);
    if (on && current && current.charts === false) {
      setModel("auto");
      setModelNote(`Charts use ${defaultLabel} — ${current.label} can't build them.`);
    }
  }

  function toggleDeepMode(on: boolean) {
    setDeepMode(on);
    if (on) {
      toggleChartMode(false);
      setActMode(false);
    } else setExtraEffort(false);
  }

  function toggleActMode(on: boolean) {
    setActMode(on);
    if (on) {
      setChartMode(false);
      setDeepMode(false);
      setExtraEffort(false);
      setModelNote(null);
    }
  }

  return (
    <AppShell me={me} variant="app">
      <div className="chat-page">
        {dragging && (
          <div className="chat-drop-overlay" aria-hidden="true">
            <div className="chat-drop-card">
              <strong>Drop to attach</strong>
              <span>
                {chartMode
                  ? "Excel, CSV, PDF or Word tables become charts in this chat."
                  : "PDF, Word, Excel, CSV, text or Markdown, private to you in this chat."}
              </span>
            </div>
          </div>
        )}
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
            {/* The scope as one small chip; the page's heading is the greeting
                below (or, mid-chat, a heading for screen readers only). Which
                source answered is stated per ANSWER, not per page. */}
            {workspaceId ? (
              <button type="button" className="ask-scope" onClick={() => setPanelOpen(true)}>
                <span className="ask-scope-dot" aria-hidden />
                {workspaceName || "Space"}
              </button>
            ) : (
              <span className="ask-scope">
                <span className="ask-scope-dot" aria-hidden />
                Company-wide
              </span>
            )}
            {messages.length > 0 && <h1 className="sr-only">Ask</h1>}
          </div>

          <div className="chat-topbar-actions">
            {(messages.length > 0 || activeConversation != null) && (
              <button
                type="button"
                className="chat-new-btn"
                onClick={startNewChat}
                title="Start a new chat"
                aria-label="Start a new chat"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
                  <line x1="12" y1="5" x2="12" y2="19" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
                  <line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
                </svg>
                <span>New chat</span>
              </button>
            )}

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
            <div className="chat-empty ask-empty">
              <AskGreeting
                name={mode === "ask" ? firstName : null}
                title={mode === "ask" ? null : emptyTitle}
                copy={emptyCopy}
              />
              {actMode ? (
                <AgentIntro
                  examples={[
                    linearAvailable && { glyph: "linear" as const, text: "File a Linear issue about the login timeout" },
                    codeAvailable && { glyph: "github" as const, text: "Open a GitHub issue for the flaky checkout test" },
                    slackAvailable && { glyph: "slack" as const, text: "Post a reminder about Friday’s demo in Slack" },
                    driveAvailable && { glyph: "drive" as const, text: "Add this week’s numbers to a Google Sheet" },
                  ].filter((e): e is { glyph: "linear" | "github" | "slack" | "drive"; text: string } => Boolean(e))}
                  onPick={(text) => {
                    setInput(text);
                    askInputRef.current?.focus();
                  }}
                />
              ) : suggestionsLoading && !chartMode ? (
                <p className="muted suggested-loading">Loading suggestions…</p>
              ) : chartMode && chartStarters === null ? (
                <div className="ask-charts" aria-busy="true" aria-label="Loading chart ideas">
                  {[0, 1, 2, 3, 4, 5].map((i) => (
                    <div key={i} className="ask-chart is-loading" style={{ ["--i" as string]: i }} aria-hidden>
                      <span className="ask-chart-thumb" />
                      <span className="ask-chart-line" />
                      <span className="ask-chart-line is-short" />
                    </div>
                  ))}
                </div>
              ) : chartMode && starters.length > 0 ? (
                <div className="ask-charts">
                  {starters.slice(0, 6).map((q, i) => (
                    <button key={q} type="button" className="ask-chart" style={{ ["--i" as string]: i }} onClick={() => ask(q)}>
                      <ChartThumb kind={chartKind(q)} />
                      <span>{q}</span>
                    </button>
                  ))}
                </div>
              ) : starters.length > 0 ? (
                <div className={deepMode ? "ask-starters is-deep" : "ask-starters"}>
                  {starters.slice(0, 6).map((q, i) => {
                    const glyph = chartMode ? undefined : SOURCE_GLYPH[suggestionSources[q] ?? ""];
                    return (
                      <button
                        key={q}
                        type="button"
                        className="ask-starter"
                        style={{ ["--i" as string]: i }}
                        onClick={() => ask(q)}
                      >
                        <span className="ask-starter-mark">
                          {glyph ? <BrandGlyph name={glyph} size={16} /> : <ChipIcon kind={chartMode ? "chart" : "policy"} />}
                        </span>
                        <span className="ask-starter-text">{q}</span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="chat-log" ref={logRef} aria-live="polite">
              {messages.map((m, i) => (
                <div key={i} className="chat-turn">
                <ChatMessageView
                  message={m}
                  conversationId={conversationId.current}
                  // The question is the previous message; a bubble holds only one.
                  question={messages[i - 1]?.role === "user" ? messages[i - 1].text : ""}
                  workspaceId={workspaceId}
                  onEnableChart={() => toggleChartMode(true)}
                  onDisableChart={() => toggleChartMode(false)}
                  onAskDeep={() => {
                    const q = messages[i - 1]?.role === "user" ? messages[i - 1].text : "";
                    toggleDeepMode(true);
                    void ask(q, "deep");
                  }}
                />
                {m.acts?.map((card) => (
                  <div className="chat-act-card" key={card.id}>
                    <ActionCard
                      card={card}
                      model={model}
                      onChange={(next) =>
                        setMessages((prev) =>
                          prev.map((x, j) =>
                            j === i
                              ? { ...x, acts: x.acts?.map((c) => (c.id === next.id ? next : c)) }
                              : x,
                          ),
                        )
                      }
                      // A chain's next step arrives as the next reply, under this one.
                      onNext={(next) =>
                        setMessages((prev) => [
                          ...prev,
                          { role: "assistant", text: next.reply, acts: next.actions.length ? next.actions : null },
                        ])
                      }
                    />
                  </div>
                ))}
                </div>
              ))}
              <div ref={bottomRef} aria-hidden className="chat-scroll-anchor" />
            </div>
          )}
        </div>

        {(attachments.length > 0 || uploadError || pending.length > 0) && (
          <div className="attach-row">
            {pending.map((name) => (
              <span key={`pending-${name}`} className="attach-chip is-pending">
                <span className="attach-spinner" aria-hidden />
                <span className="attach-kind">{fileKind(name)}</span>
                <span className="attach-name" title={name}>
                  {name}
                </span>
                <span className="attach-cut">uploading…</span>
              </span>
            ))}
            {attachments.map((a) => (
              <span key={a.id} className="attach-chip">
                {/* The FORMAT, not a paperclip. A clip repeats what the row
                    already says ("there is a file"); the format says what kind
                    of file the answer is built on, which is the thing worth
                    checking. */}
                <span className="attach-kind">{fileKind(a.filename)}</span>
                <span className="attach-name" title={a.filename}>
                  {a.filename}
                </span>
                {a.truncated && (
                  <span
                    className="attach-cut"
                    title="This file was long, so only the first part is being read."
                  >
                    part only
                  </span>
                )}
                {a.flagged && (
                  <span
                    className="attach-cut"
                    title="Parts of this file read like instructions to an AI. It is still read, as data only."
                  >
                    check file
                  </span>
                )}
                <button
                  type="button"
                  className="attach-remove"
                  onClick={() => detach(a.id)}
                  aria-label={`Remove ${a.filename}`}
                >
                  ×
                </button>
              </span>
            ))}
            {pending.length > 0 && (
              <span className="attach-note" role="status" aria-live="polite">
                Uploading — your question will use {pending.length === 1 ? "this file" : "these files"} once it finishes.
              </span>
            )}
            {attachments.length > 0 && pending.length === 0 && (
              <span className="attach-note">
                {chartMode
                  ? `Charts can use the tables and figures in ${attachments.length === 1 ? "this file" : "these files"}, and your connected tools.`
                  : `Answers can use ${attachments.length === 1 ? "this file" : "these files"} and your connected tools together.`}
              </span>
            )}
            {uploadError && <span className="attach-error">{uploadError}</span>}
          </div>
        )}

        <div className="ask-compose-head">
          <ModeSwitch
            mode={mode}
            agent={Boolean(me.act_enabled && spaceActOn)}
            disabled={busy}
            onPick={(m) => {
              if (m === "ask") {
                toggleChartMode(false);
                toggleDeepMode(false);
                toggleActMode(false);
              } else if (m === "deep") toggleDeepMode(true);
              else if (m === "chart") toggleChartMode(true);
              else toggleActMode(true);
              askInputRef.current?.focus();
            }}
          />
          {mode === "deep" && (
            <button
              type="button"
              className="ask-effort"
              aria-pressed={extraEffort}
              disabled={busy}
              title="Reads many documents, not only the closest ones. Uses more of your AI quota."
              onClick={() => setExtraEffort((on) => !on)}
            >
              <DeepIcon />
              Extra effort
            </button>
          )}
          <ReleaseChip admin={me.role === "admin"} />
        </div>
        <form onSubmit={handleSubmit} className={`chat-composer is-${mode}${extraEffort ? " is-extra" : ""}`} aria-label="Ask a question">
          <label className="sr-only" htmlFor="ask-input">
            Your question
          </label>
          <input
            ref={fileInput}
            type="file"
            className="sr-only"
            accept=".pdf,.docx,.xlsx,.csv,.tsv,.txt,.md,.markdown,.log,.json"
            multiple
            onChange={(e) => attach(e.target.files)}
          />
          <div className="composer-tools">
            <button
              type="button"
              className="chat-composer-attach"
              onClick={() => fileInput.current?.click()}
              disabled={busy || uploading}
              aria-label="Add files"
              title="Add files"
            >
              {uploading ? <span className="composer-spinner" aria-hidden /> : <PlusIcon />}
            </button>
          </div>
          <textarea
            ref={askInputRef}
            id="ask-input"
            className="chat-composer-input"
            rows={1}
            placeholder={mode === "ask" ? typedPlaceholder : composerPlaceholder}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault();
                if (!busy && !uploading && input.trim()) {
                  e.currentTarget.form?.requestSubmit();
                }
              }
            }}
            // A file copied in Finder/Explorer and pasted here attaches it;
            // pasted TEXT is left to the input as usual.
            onPaste={(e) => {
              if (e.clipboardData.files.length > 0) {
                e.preventDefault();
                void attach(e.clipboardData.files);
              }
            }}
            disabled={busy}
            autoFocus
          />
          {models.length > 0 && (
            <>
              <ModelPicker
                value={model}
                disabled={busy}
                title={models.find((m) => m.id === model)?.note ?? `Answer with the default model (${defaultLabel})`}
                options={[
                  { value: "auto", label: modelName(defaultLabel), hint: "Default" },
                  ...pickerModels.map((m) => ({
                    value: m.id,
                    label: modelName(m.label),
                    hint: chartMode && m.charts == null ? "May not support charts" : undefined,
                  })),
                ]}
                onChange={(next) => {
                  setModel(next);
                  setModelNote(null);
                  localStorage.setItem("chat.model", next);
                }}
              />
            </>
          )}
          <button
            className="chat-composer-send"
            type="submit"
            disabled={busy || uploading || !input.trim()}
            aria-label="Send question"
            title={
              uploading
                ? "Waiting for the upload to finish — otherwise this question wouldn't see the file"
                : undefined
            }
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
        {actMode ? (
          <p className="muted chat-model-note" role="status">
            Agent mode drafts up to five actions per request and sends nothing until you approve each one. Drafts aren&rsquo;t saved
            in this chat; find them under <Link href="/account#act">Account → Agent</Link>.
          </p>
        ) : (chartMode || modelNote) && (
          <p className="muted chat-model-note" role="status">
            {modelNote ??
              "Chart mode only builds charts. Turn it off for a written answer."}
          </p>
        )}
      </div>
    </AppShell>
  );
}
