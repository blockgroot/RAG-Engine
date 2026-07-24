import { ChatDonePayload } from "@/lib/sse";
import { CitationPanel } from "./CitationPanel";
import { ProvenanceStripe } from "./ProvenanceStripe";

export interface Message {
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  done?: ChatDonePayload;
}

export function ChatMessageView({ message }: { message: Message }) {
  if (message.role === "user") {
    return (
      <div style={{ textAlign: "right" }}>
        <div
          className="card"
          style={{ display: "inline-block", background: "var(--accent)", color: "var(--accent-ink)", border: "none" }}
        >
          {message.text}
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      {message.done && <ProvenanceStripe source={message.done.source} />}
      <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>
        {message.text}
        {message.streaming && <span aria-hidden>▍</span>}
      </p>
      {message.done && <CitationPanel citations={message.done.citations} />}
    </div>
  );
}
