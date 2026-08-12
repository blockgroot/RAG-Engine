/**
 * Renders the model's answer as basic formatted text instead of a raw
 * pre-wrapped string. The grounded-generation prompt (app/rag/prompts.py)
 * produces simple markdown — **bold**, "- "/"* "/"1. " lists — which a plain
 * <p> does not render usefully. Any leftover ``[n]`` markers from older
 * prompt versions are stripped so they do not show up as noise.
 *
 * Parsing is line-oriented: a mixed block of prose + bullets must NOT collapse
 * into one HTML paragraph (browsers collapse `\n` inside `<p>` to spaces),
 * which is what made answers look like a single cluttered run-on sentence.
 */

import { Fragment, type ReactNode } from "react";

const CITATION_MARKERS = /\s?(\[\d+\])+/g;
const BULLET = /^\s*(?:[-*]|\d+\.)\s+(.*)$/;

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
  | { kind: "list"; items: string[] };

/** Split answer text into alternating paragraph / list blocks. */
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
