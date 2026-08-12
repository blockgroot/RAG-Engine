/**
 * Minimal SSE client over `fetch` (not `EventSource`) — `EventSource` can't
 * send a POST body or attach custom logic per chunk, and we need to POST the
 * question. Parses `event: <name>\ndata: <payload>\n\n` blocks as they arrive.
 */

import { API_BASE_URL } from "./api";

export interface ChatDonePayload {
  answer: string;
  grounded: boolean;
  source: "policy" | "workspace" | "web" | "github" | "none";
  /** Optional — kept for API compatibility; not shown in the UI. */
  citations?: { content: string; reference: string; score: number | null }[];
  resolved_question: string | null;
  latency_ms: number | null;
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
  /**
   * Which agent answers. Omitted (or "policy") uses the indexed policy corpus;
   * "github" routes to the GitHub agent, which answers from live GitHub API
   * reads instead of retrieval. The client names this explicitly because
   * GitHub connects at the org level, so an org commonly has policies AND
   * GitHub connected at once and "route by connected source" cannot
   * disambiguate. The server decides deterministically from this value -- no
   * LLM classifies the question.
   */
  agent?: "policy" | "github"
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
