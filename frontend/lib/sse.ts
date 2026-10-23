import { API_BASE_URL, type ActCard, type InsightPanel } from "./api";

export interface CitedSource {
  n: number;
  document_id: string;
  title: string | null;
  provider: string | null;
  url: string | null;
}

export interface ChatDonePayload {
  answer: string;
  grounded: boolean;
  source:
    | "policy"
    | "workspace"
    | "web"
    | "github"
    | "slack"
    | "linear"
    | "notion"
    | "google"
    | "forms"
    | "none";
  citations?: { content: string; reference: string; score: number | null }[];
  resolved_question: string | null;
  latency_ms: number | null;
  /** Present when Ask answered from activity_facts instead of RAG. */
  chart?: InsightPanel | null;
  chart_period?: string;
  /** The model that actually answered, resolved by the endpoint. Null on the
   *  default path — there is nothing to disclose when nobody chose. */
  model?: string | null;
  /** WHICH agent answered. The member no longer picks a source, so this is the
   *  only way "where did this come from?" is answerable. */
  agent?:
    | "policy"
    | "workspace"
    | "github"
    | "slack"
    | "linear"
    | "notion"
    | "google"
    | "forms"
    | "insights";
  /** Filenames that were in this prompt. Present only when the asker had files
   *  attached; the pill names them instead of claiming the routed source. */
  attachments?: string[];
  /** Why that agent was picked — "best-match", "repo-named", "code-intent",
   *  "only-source", "weak-best-match", "requested", "no-sources". Surfaced so
   *  a misroute is distinguishable from a source genuinely lacking the answer. */
  routing_reason?: string;
  /** The tools a CONNECTED answer read (e.g. ["notion","slack"]); null for a
   *  normal answer. The pill names all of them, or it would credit one tool
   *  with an answer drawn from two. */
  connected_providers?: string[] | null;
  /** Connectors read LIVE for this answer (Second Brain live tools). Empty when the
   *  indexed copy answered, so "live" is never claimed for a synced copy. */
  live_sources?: { provider: string; fetched_at: string }[];
  /** The documents behind the answer's [n] markers, numbered in reading order.
   *  The server keeps only numbers that pointed at a retrieved document, and
   *  `url` comes from the stored document, never from the model's text. */
  cited?: CitedSource[];
  /** Personal memory saved from THIS question -- shown with Undo, so saving
   *  is never silent. */
  remembered?: { id: string; text: string }[];
  /** Asked for a chart (or a count) outside Chart mode: offer to turn it on. */
  chart_hint?: boolean;
  /** A text question asked in Chart mode: offer to turn Chart off. */
  ask_hint?: boolean;
  /** Refused in normal Ask though relevant documents were found: offer Deep analysis. */
  deep_hint?: boolean;
}

export interface ChatStreamHandlers {
  onToken: (chunk: string) => void;
  /** A step the answer is taking right now ("Searching Notion"), from the server. */
  onStatus?: (line: string) => void;
  onDone: (payload: ChatDonePayload) => void;
  onError: (message: string) => void;
}

export async function streamChat(
  question: string,
  conversationId: string | null,
  handlers: ChatStreamHandlers,
  workspaceId?: string | null,
  agent?: "policy" | "github" | "slack" | "linear" | "notion" | "google",
  model?: string | null,
  /** "chart" = Chart mode: the answer is a chart or a plain refusal.
   *  "deep" = Deep analysis: the top documents are read more fully.
   *  "deep_extra" = Deep analysis with extra effort: many documents as well,
   *  for company-wide and list questions, at more tokens. */
  mode?: "chart" | "deep" | "deep_extra",
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/chat/stream`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        conversation_id: conversationId,
        ...(workspaceId ? { workspace_id: workspaceId } : {}),
        ...(agent && agent !== "policy" ? { agent } : {}),
        // Omitted entirely on "auto" so the request is byte-identical to one
        // sent before this feature existed.
        ...(model && model !== "auto" ? { model } : {}),
        ...(mode ? { mode } : {}),
      }),
    });
  } catch {
    handlers.onError("Could not reach the server.");
    return;
  }

  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({ detail: "Request failed" }));
    handlers.onError(body.detail || "Request failed");
    return;
  }

  for await (const { event, data } of sseEvents(response.body)) {
    if (event === "token") {
      handlers.onToken(JSON.parse(data) as string);
    } else if (event === "status") {
      handlers.onStatus?.(JSON.parse(data) as string);
    } else if (event === "done") {
      handlers.onDone(JSON.parse(data) as ChatDonePayload);
      return;
    } else if (event === "error") {
      const payload = JSON.parse(data) as { message?: string };
      handlers.onError(payload.message || "Something went wrong.");
      return;
    }
  }

  handlers.onError("The answer stream ended unexpectedly. Please try again.");
}

/** Each ``event:``/``data:`` block of a server-sent event stream, as it arrives. */
async function* sseEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<{ event: string; data: string }> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const lines = buffer.slice(0, boundary).split("\n");
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
      const eventLine = lines.find((l) => l.startsWith("event: "));
      const dataLine = lines.find((l) => l.startsWith("data: "));
      if (eventLine && dataLine) {
        yield { event: eventLine.slice("event: ".length), data: dataLine.slice("data: ".length) };
      }
    }
  }
}

export interface ActReply {
  reply: string;
  action: ActCard | null;
  actions?: ActCard[];
}

/** Agent mode, streamed: ``onStatus`` gets each step it takes while drafting
 *  ("Looking up in Linear: List teams"); resolves with the drafts. Throws an
 *  ``Error`` whose ``status`` is the HTTP code (503: the model is down). */
export async function streamAct(
  question: string,
  workspaceId: string | null | undefined,
  model: string | null | undefined,
  onStatus: (line: string) => void,
): Promise<ActReply> {
  const response = await fetch(`${API_BASE_URL}/act/stream`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      ...(workspaceId ? { workspace_id: workspaceId } : {}),
      ...(model && model !== "auto" ? { model } : {}),
    }),
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({ detail: "Request failed" }));
    throw Object.assign(new Error(typeof body.detail === "string" ? body.detail : "Request failed"), {
      status: response.status,
    });
  }
  for await (const { event, data } of sseEvents(response.body)) {
    if (event === "status") onStatus(JSON.parse(data) as string);
    else if (event === "done") return JSON.parse(data) as ActReply;
    else if (event === "error") {
      const payload = JSON.parse(data) as { message?: string; status?: number };
      throw Object.assign(new Error(payload.message || "Something went wrong. Nothing was sent."), {
        status: payload.status ?? 500,
      });
    }
  }
  throw Object.assign(new Error("The draft stream ended unexpectedly. Nothing was sent."), { status: 500 });
}

/** After a card is approved or cancelled: what the request drafts next
 *  ("then post its link"), streamed like ``streamAct`` because drafting it
 *  can outlast the web proxy. ``null`` when nothing continues. */
export async function streamContinue(
  actionId: string,
  onStatus: (line: string) => void,
): Promise<ActReply | null> {
  const response = await fetch(`${API_BASE_URL}/act/actions/${encodeURIComponent(actionId)}/continue/stream`, {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok || !response.body) return null;
  for await (const { event, data } of sseEvents(response.body)) {
    if (event === "status") onStatus(JSON.parse(data) as string);
    else if (event === "done") return JSON.parse(data) as ActReply | null;
    else if (event === "error") return null;
  }
  return null;
}
