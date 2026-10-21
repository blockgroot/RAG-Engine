import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

/** One question per tool that a new team already knows the answer to. */
const STARTERS: Array<[BrandName, string]> = [
  ["notion", "How many sick days do we get?"],
  ["drive", "Where is the Q3 budget?"],
  ["slack", "What did #launch decide on Friday?"],
  ["linear", "Which payment issues are blocked?"],
  ["github", "Who owns the checkout repo?"],
];

/**
 * Closing figure: an empty composer and a first question for each tool. The
 * heading says "ask a question you already know"; this shows what one looks
 * like, whichever tool they connect first.
 */
export function CloseFigure() {
  return (
    <div className="site-frame starter" aria-hidden>
      <div className="starter-box">
        <span className="hero-mark">HB</span>
        <span className="starter-input">
          Ask your workplace anything<i className="starter-caret" />
        </span>
        <b>&uarr;</b>
      </div>
      <p className="f-mono starter-label">Try one of these first</p>
      <ul className="starter-list">
        {STARTERS.map(([tool, q]) => (
          <li key={tool}>
            <BrandGlyph name={tool} size={15} />
            <span>{q}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
