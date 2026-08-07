import { ChatDonePayload } from "@/lib/sse";
import { AnswerText } from "./AnswerText";
import { ProvenanceStripe } from "./ProvenanceStripe";

export interface Message {
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  done?: ChatDonePayload;
}

/**
 * Chat bubble for Ask. Citations stay on the API payload for grounding /
 * debugging, but are not rendered — employees only need the answer (and a
 * small provenance pill so policy vs web vs GitHub is still clear).
 */
export function ChatMessageView({ message }: { message: Message }) {
  if (message.role === "user") {
    return <div className="chat-bubble chat-bubble-user">{message.text}</div>;
  }

  const thinking = Boolean(message.streaming && !message.text.trim());

  return (
    <div className="chat-bubble chat-bubble-assistant" data-thinking={thinking || undefined}>
      {message.done && <ProvenanceStripe source={message.done.source} />}
      {thinking ? (
        <div className="chat-thinking" role="status" aria-live="polite">
          <span className="chat-thinking-dots" aria-hidden>
            <span />
            <span />
            <span />
          </span>
          <span className="chat-thinking-label">Finding a grounded answer…</span>
        </div>
      ) : (
        <>
          <AnswerText text={message.text} />
          {message.streaming && <span className="chat-stream-caret" aria-hidden />}
        </>
      )}
    </div>
  );
}
