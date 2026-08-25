import { Fragment, type ReactNode } from "react";

const CITATION_MARKERS = /\s?(\[\d+\])+/g;
const BULLET = /^\s*(?:[-*]|\d+\.)\s+(.*)$/;
// Nothing asks the model for headings, but a report-length answer routinely
// emits them anyway — and an unhandled "## What shipped" renders as literal
// hashes in the middle of the page. Cheaper to render them than to strip them.
const HEADING = /^\s{0,3}(#{1,4})\s+(.*)$/;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={`${keyPrefix}-${i}`}>{part.slice(2, -2)}</strong>
    ) : (
      <Fragment key={`${keyPrefix}-${i}`}>{part}</Fragment>
    )
  );
}

type Block =
  | { kind: "paragraph"; text: string }
  | { kind: "heading"; text: string; level: number }
  | { kind: "list"; items: string[] };

function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let paragraphLines: string[] = [];
  let listItems: string[] = [];

  function flushParagraph() {
    const joined = paragraphLines.join(" ").replace(/\s+/g, " ").trim();
    paragraphLines = [];
    if (joined) blocks.push({ kind: "paragraph", text: joined });
  }

  function flushList() {
    if (listItems.length === 0) return;
    blocks.push({ kind: "list", items: listItems });
    listItems = [];
  }

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (line.trim() === "") {
      flushList();
      flushParagraph();
      continue;
    }
    const heading = line.match(HEADING);
    if (heading) {
      flushList();
      flushParagraph();
      blocks.push({
        kind: "heading",
        // h1/h2 belong to the page, never to answer text — clamp so a model
        // writing "#" cannot outrank the page title in the outline.
        level: Math.min(4, Math.max(3, heading[1].length + 2)),
        text: heading[2].trim(),
      });
      continue;
    }
    const bullet = line.match(BULLET);
    if (bullet) {
      flushParagraph();
      listItems.push(bullet[1].trim());
      continue;
    }
    flushList();
    paragraphLines.push(line.trim());
  }
  flushList();
  flushParagraph();
  return blocks;
}

export function AnswerText({ text }: { text: string }) {
  const cleaned = text.replace(CITATION_MARKERS, "");
  const blocks = parseBlocks(cleaned);

  return (
    <div className="chat-answer">
      {blocks.map((block, blockIndex) => {
        if (block.kind === "heading") {
          const Tag = block.level === 3 ? "h3" : "h4";
          return (
            <Tag key={blockIndex} className="answer-heading">
              {renderInline(block.text, `${blockIndex}`)}
            </Tag>
          );
        }
        if (block.kind === "list") {
          return (
            <ul key={blockIndex} className="answer-list">
              {block.items.map((item, i) => (
                <li key={i}>{renderInline(item, `${blockIndex}-${i}`)}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={blockIndex} className="answer-paragraph">
            {renderInline(block.text, `${blockIndex}`)}
          </p>
        );
      })}
    </div>
  );
}
