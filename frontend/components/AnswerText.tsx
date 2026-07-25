/**
 * Renders the model's answer as basic formatted text instead of a raw
 * pre-wrapped string. The grounded-generation prompt (app/rag/prompts.py)
 * produces simple markdown — **bold**, "- "/"* " bullet lists, and inline
 * [n] citation markers — none of which a plain <p> renders usefully. Citation
 * markers are stripped here: with the sources panel removed from the UI (per
 * user feedback, they're retrieval-debugging detail, not something an
 * employee needs), a bare "[1][2]" floating in the text is just noise.
 */

const CITATION_MARKERS = /\s?(\[\d+\])+/g;

function renderInline(text: string, keyPrefix: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={`${keyPrefix}-${i}`}>{part.slice(2, -2)}</strong>
    ) : (
      <span key={`${keyPrefix}-${i}`}>{part}</span>
    )
  );
}

export function AnswerText({ text }: { text: string }) {
  const cleaned = text.replace(CITATION_MARKERS, "");
  const blocks = cleaned.split(/\n{2,}/);

  return (
    <div className="chat-answer">
      {blocks.map((block, blockIndex) => {
        const lines = block.split("\n").filter((l) => l.trim() !== "");
        const isList = lines.length > 0 && lines.every((l) => /^\s*[*-]\s+/.test(l));

        if (isList) {
          return (
            <ul key={blockIndex} className="answer-list">
              {lines.map((line, i) => (
                <li key={i}>{renderInline(line.replace(/^\s*[*-]\s+/, ""), `${blockIndex}-${i}`)}</li>
              ))}
            </ul>
          );
        }

        return (
          <p key={blockIndex} className="answer-paragraph">
            {renderInline(block, `${blockIndex}`)}
          </p>
        );
      })}
    </div>
  );
}
