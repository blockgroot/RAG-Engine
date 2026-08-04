import { ChatDonePayload } from "@/lib/sse";
import { AnswerText } from "./AnswerText";
import { ProvenanceStripe } from "./ProvenanceStripe";

export interface Message {
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  done?: ChatDonePayload;
}

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
          <span className="chat-thinking-label">Looking through your documents…</span>
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
