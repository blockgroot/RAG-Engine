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
    return (
      <div className="chat-bubble chat-bubble-user">
        {message.text}
      </div>
    );
  }

  return (
    <div className="chat-bubble chat-bubble-assistant">
      {message.done && <ProvenanceStripe source={message.done.source} />}
      <AnswerText text={message.text} />
      {message.streaming && <span aria-hidden>▍</span>}
    </div>
  );
}
