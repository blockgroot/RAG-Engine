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
 * Chat bubble for Ask. Shows the answer plus a small provenance pill
 * (policy vs web vs GitHub) when the stream finishes, and — when the member
 * picked a model — which model actually answered.
 */
export function ChatMessageView({ message }: { message: Message }) {
  if (message.role === "user") {
    return <div className="chat-bubble chat-bubble-user">{message.text}</div>;
  }

  const thinking = Boolean(message.streaming && !message.text.trim());

  return (
    <div className="chat-bubble chat-bubble-assistant" data-thinking={thinking || undefined}>
      {message.done && (
        <ProvenanceStripe source={message.done.source} agent={message.done.agent} />
      )}
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
          {/* Only when a model was actually selected. On the default path the
              backend sends null, and naming the deployment's model to every
              member would be noise, not provenance. */}
          {message.done?.model && (
            <span className="chat-model-tag">Answered by {message.done.model}</span>
          )}
        </>
      )}
    </div>
  );
}
