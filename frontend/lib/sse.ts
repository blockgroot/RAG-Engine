import { API_BASE_URL, type InsightPanel } from "./api";

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
    | "forms";
  /** Why that agent was picked — "best-match", "repo-named", "code-intent",
   *  "only-source", "weak-best-match", "requested", "no-sources". Surfaced so
   *  a misroute is distinguishable from a source genuinely lacking the answer. */
  routing_reason?: string;
}

export interface ChatStreamHandlers {
  onToken: (chunk: string) => void;
  onDone: (payload: ChatDonePayload) => void;
  onError: (message: string) => void;
}

export async function streamChat(
  question: string,
  conversationId: string | null,
  handlers: ChatStreamHandlers,
  workspaceId?: string | null,
  agent?: "policy" | "github" | "slack" | "linear" | "notion" | "google",
  model?: string | null
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

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");

      const lines = block.split("\n");
      const eventLine = lines.find((l) => l.startsWith("event: "));
      const dataLine = lines.find((l) => l.startsWith("data: "));
      if (!eventLine || !dataLine) continue;

      const event = eventLine.slice("event: ".length);
      const data = dataLine.slice("data: ".length);

      if (event === "token") {
        handlers.onToken(JSON.parse(data) as string);
      } else if (event === "done") {
        handlers.onDone(JSON.parse(data) as ChatDonePayload);
        return;
      } else if (event === "error") {
        const payload = JSON.parse(data) as { message?: string };
        handlers.onError(payload.message || "Something went wrong.");
        return;
      }
    }
  }

  handlers.onError("The answer stream ended unexpectedly. Please try again.");
}
