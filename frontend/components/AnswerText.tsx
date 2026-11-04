import { Fragment, type ReactNode } from "react";
import type { CitedSource } from "@/lib/sse";
import { LABELS } from "./ProvenanceStripe";

const CITATION_MARKERS = /\s?(\[\d+\])+/g;
const MARKER = /\[(\d+)\]/g;
const BULLET = /^\s*(?:[-*]|\d+\.)\s+(.*)$/;
// Nothing asks the model for headings, but a report-length answer routinely
// emits them anyway — and an unhandled "## What shipped" renders as literal
// hashes in the middle of the page. Cheaper to render them than to strip them.
const HEADING = /^\s{0,3}(#{1,4})\s+(.*)$/;

type Sources = Map<number, CitedSource>;

function sourceTitle(s: CitedSource): string {
  return s.title || "Untitled document";
}

function sourceApp(s: CitedSource): string | null {
  return s.provider ? LABELS[s.provider] || s.provider : null;
}

function sourceLabel(s: CitedSource): string {
  return [sourceTitle(s), sourceApp(s)].filter(Boolean).join(" · ");
}

function CitationMark({ source }: { source: CitedSource }) {
  const label = sourceLabel(source);
  // Superscript, the usual reference mark. A filled chip on every sentence
  // reads as a button and crowds a list that all cites one page; the document
  // is named once, under the answer. Only a link taken from the stored document.
  const mark = <sup>{source.n}</sup>;
  return source.url ? (
    <a
      className="cite-ref"
      href={source.url}
      target="_blank"
      rel="noopener noreferrer"
      title={label}
      aria-label={`Source ${source.n}: ${label}`}
    >
      {mark}
    </a>
  ) : (
    <span className="cite-ref" title={label} aria-label={`Source ${source.n}: ${label}`}>
      {mark}
    </span>
  );
}

function renderMarkers(text: string, keyPrefix: string, sources: Sources): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(MARKER)) {
    const source = sources.get(Number(m[1]));
    if (m.index! > last) out.push(text.slice(last, m.index));
    if (source) out.push(<CitationMark key={`${keyPrefix}-c${m.index}`} source={source} />);
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function renderInline(text: string, keyPrefix: string, sources: Sources): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={`${keyPrefix}-${i}`}>{part.slice(2, -2)}</strong>
    ) : (
      <Fragment key={`${keyPrefix}-${i}`}>
        {renderMarkers(part, `${keyPrefix}-${i}`, sources)}
      </Fragment>
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

export function AnswerText({ text, cited }: { text: string; cited?: CitedSource[] }) {
  // Without a citation map (still streaming, or a turn saved before sources
  // were kept) every marker is stripped. One document is named once under the
  // answer, so the marks in the sentences are stripped too. Two or more keep
  // a superscript so a sentence can point at the right line.
  const sources: Sources = new Map((cited || []).map((s) => [s.n, s]));
  const inline: Sources = sources.size > 1 ? sources : new Map();
  const cleaned = inline.size ? text : text.replace(CITATION_MARKERS, "");
  const blocks = parseBlocks(cleaned);

  return (
    <div className="chat-answer">
      {blocks.map((block, blockIndex) => {
        if (block.kind === "heading") {
          const Tag = block.level === 3 ? "h3" : "h4";
          return (
            <Tag key={blockIndex} className="answer-heading">
              {renderInline(block.text, `${blockIndex}`, inline)}
            </Tag>
          );
        }
        if (block.kind === "list") {
          return (
            <ul key={blockIndex} className="answer-list">
              {block.items.map((item, i) => (
                <li key={i}>{renderInline(item, `${blockIndex}-${i}`, inline)}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={blockIndex} className="answer-paragraph">
            {renderInline(block.text, `${blockIndex}`, inline)}
          </p>
        );
      })}
      {sources.size > 0 && (
        <section className="answer-sources" aria-label="Sources">
          <p className="answer-sources-label">Sources</p>
          <ol>
            {[...sources.values()].map((s) => {
              const app = sourceApp(s);
              const body = (
                <>
                  <span className="answer-source-title">{sourceTitle(s)}</span>
                  {app && <span className="answer-source-app">{app}</span>}
                </>
              );
              return (
                <li key={s.n}>
                  <span className="answer-source-n">{s.n}.</span>
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noopener noreferrer">
                      {body}
                    </a>
                  ) : (
                    <span className="answer-source-plain">{body}</span>
                  )}
                </li>
              );
            })}
          </ol>
        </section>
      )}
    </div>
  );
}

function renderBold(text: string, keyPrefix: string): ReactNode[] {
  return text
    .split(/(\*\*[^*]+\*\*)/g)
    .filter(Boolean)
    .map((part, i) =>
      part.startsWith("**") && part.endsWith("**") ? (
        <strong key={`${keyPrefix}-${i}`}>{part.slice(2, -2)}</strong>
      ) : (
        <Fragment key={`${keyPrefix}-${i}`}>{part}</Fragment>
      )
    );
}

/** A draft's Markdown as the tool will show it (bold, bullets, headings).
 *  Unlike an answer, nothing is stripped: "[1]" in a draft is text that will
 *  be sent, so the card must show it. */
export function DraftText({ text }: { text: string }) {
  // A draft that is one Markdown table (a Sheet) is shown as that table.
  const lines = text.trim().split("\n");
  if (lines.length > 1 && lines.every((l) => l.trim().startsWith("|"))) {
    const rows = lines
      .map((l) => l.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|")))
      .filter((cells) => !cells.every((c) => /^:?-{3,}:?$/.test(c)));
    const [head, ...body] = rows;
    return (
      <div className="draft-table-wrap">
        <table className="draft-table">
          <thead>
            <tr>{head.map((c, i) => <th key={i}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {body.map((r, i) => (
              <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <div className="chat-answer">
      {parseBlocks(text).map((block, i) => {
        if (block.kind === "heading") {
          const Tag = block.level === 3 ? "h3" : "h4";
          return (
            <Tag key={i} className="answer-heading">
              {renderBold(block.text, `${i}`)}
            </Tag>
          );
        }
        if (block.kind === "list") {
          return (
            <ul key={i} className="answer-list">
              {block.items.map((item, j) => {
                // "- [ ] task" is a checklist item in Linear, Notion and GitHub.
                const task = item.match(/^\[([ xX])\]\s+(.*)$/);
                return task ? (
                  <li key={j} className="draft-task">
                    <input type="checkbox" checked={task[1] !== " "} disabled readOnly aria-label={task[2]} />{" "}
                    {renderBold(task[2], `${i}-${j}`)}
                  </li>
                ) : (
                  <li key={j}>{renderBold(item, `${i}-${j}`)}</li>
                );
              })}
            </ul>
          );
        }
        return (
          <p key={i} className="answer-paragraph">
            {renderBold(block.text, `${i}`)}
          </p>
        );
      })}
    </div>
  );
}
