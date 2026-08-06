import { ChatDonePayload } from "@/lib/sse";
import { AnswerText } from "./AnswerText";
import { ProviderMark } from "./ProviderMark";
import { ProvenanceStripe } from "./ProvenanceStripe";

export interface Message {
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  done?: ChatDonePayload;
}

type CitationKind = "about" | "readme" | "commit" | "document" | "generic";

interface ParsedCitation {
  kind: CitationKind;
  label: string;
  detail?: string;
}

const UUID_REF =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:#\d+)?$/i;

/** Turn raw refs into readable source-card labels (never shouty UUID dumps). */
function parseCitationRef(ref: string | undefined, index: number): ParsedCitation {
  const raw = (ref || "").trim();
  if (!raw) {
    return { kind: "generic", label: "Source", detail: `Excerpt ${index + 1}` };
  }

  const aboutOrReadme = raw.match(/^([^/#\s]+\/[^/#\s]+)#(about|readme)$/i);
  if (aboutOrReadme) {
    const repo = aboutOrReadme[1];
    const kind = aboutOrReadme[2].toLowerCase() as "about" | "readme";
    return {
      kind,
      label: kind === "about" ? "Repository about" : "README",
      detail: repo,
    };
  }

  const commitAt = raw.match(/^([^/#\s]+\/[^/#\s]+)@([0-9a-f]{7,40})$/i);
  if (commitAt) {
    return {
      kind: "commit",
      label: "Commit",
      detail: `${commitAt[1]} · ${commitAt[2].slice(0, 7)}`,
    };
  }

  const commitHash = raw.match(/^([^/#\s]+\/[^/#\s]+)#([0-9a-f]{7,40})$/i);
  if (commitHash) {
    return {
      kind: "commit",
      label: "Commit",
      detail: `${commitHash[1]} · ${commitHash[2].slice(0, 7)}`,
    };
  }

  // Policy / workspace: "Leave Policy · excerpt 2"
  const titled = raw.match(/^(.*?)\s·\sexcerpt\s+(\d+)$/i);
  if (titled) {
    return {
      kind: "document",
      label: `Excerpt ${titled[2]}`,
      detail: titled[1].trim(),
    };
  }

  if (/^Document excerpt\s+\d+$/i.test(raw)) {
    return { kind: "document", label: "Document", detail: raw };
  }

  // Legacy UUID#n refs — never show the raw id.
  if (UUID_REF.test(raw)) {
    const part = raw.includes("#") ? raw.split("#").pop() : null;
    const n = part && /^\d+$/.test(part) ? String(Number(part) + 1) : String(index + 1);
    return { kind: "document", label: "Document", detail: `Excerpt ${n}` };
  }

  return { kind: "generic", label: "Source", detail: raw };
}

function CitationCard({
  reference,
  content,
  index,
}: {
  reference?: string;
  content: string;
  index: number;
}) {
  const parsed = parseCitationRef(reference, index);
  const isGithub =
    parsed.kind === "about" || parsed.kind === "readme" || parsed.kind === "commit";
  const isDocument = parsed.kind === "document";

  return (
    <div
      className={[
        "chat-citation",
        isGithub ? "chat-citation-github" : "",
        parsed.kind === "about" ? "chat-citation-about" : "",
        isDocument ? "chat-citation-document" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="chat-citation-head">
        {isGithub && <ProviderMark provider="github" size={18} />}
        <div className="chat-citation-meta">
          <span className="chat-citation-kind">{parsed.label}</span>
          {parsed.detail ? (
            <span className="chat-citation-title">{parsed.detail}</span>
          ) : null}
        </div>
      </div>
      <p className="chat-citation-body">{content}</p>
    </div>
  );
}

export function ChatMessageView({ message }: { message: Message }) {
  if (message.role === "user") {
    return <div className="chat-bubble chat-bubble-user">{message.text}</div>;
  }

  const thinking = Boolean(message.streaming && !message.text.trim());
  const citations = message.done?.citations?.filter((c) => c.content?.trim()) ?? [];

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
          {!message.streaming && citations.length > 0 && (
            <div className="chat-citations" aria-label="Sources">
              {citations.slice(0, 4).map((c, i) => (
                <CitationCard
                  key={`${c.reference}-${i}`}
                  reference={c.reference}
                  content={c.content}
                  index={i}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
