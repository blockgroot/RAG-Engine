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

type CitationKind = "about" | "readme" | "commit" | "generic";

interface ParsedCitation {
  kind: CitationKind;
  repo?: string;
  label: string;
  detail?: string;
}

/** Turn raw refs like ``owner/repo#about`` into readable source-card labels. */
function parseCitationRef(ref: string | undefined, index: number): ParsedCitation {
  const raw = (ref || "").trim();
  if (!raw) {
    return { kind: "generic", label: `Source ${index + 1}` };
  }

  const aboutOrReadme = raw.match(/^([^/#\s]+\/[^/#\s]+)#(about|readme)$/i);
  if (aboutOrReadme) {
    const repo = aboutOrReadme[1];
    const kind = aboutOrReadme[2].toLowerCase() as "about" | "readme";
    return {
      kind,
      repo,
      label: kind === "about" ? "Repository about" : "README",
      detail: repo,
    };
  }

  const commitAt = raw.match(/^([^/#\s]+\/[^/#\s]+)@([0-9a-f]{7,40})$/i);
  if (commitAt) {
    return {
      kind: "commit",
      repo: commitAt[1],
      label: "Commit",
      detail: `${commitAt[1]} · ${commitAt[2].slice(0, 7)}`,
    };
  }

  // Commit citations sometimes use ``repo#sha`` — keep a readable fallback.
  const hashParts = raw.match(/^([^/#\s]+\/[^/#\s]+)#([0-9a-f]{7,40})$/i);
  if (hashParts) {
    return {
      kind: "commit",
      repo: hashParts[1],
      label: "Commit",
      detail: `${hashParts[1]} · ${hashParts[2].slice(0, 7)}`,
    };
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

  return (
    <div
      className={`chat-citation${isGithub ? " chat-citation-github" : ""}${
        parsed.kind === "about" ? " chat-citation-about" : ""
      }`}
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
