import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

/** The five connectors, in the order their lines meet the answer card. */
const TOOLS: Array<{ name: BrandName; label: string; state: string }> = [
  { name: "notion", label: "Notion", state: "synced" },
  { name: "drive", label: "Drive", state: "synced" },
  { name: "slack", label: "Slack", state: "synced" },
  { name: "linear", label: "Linear", state: "synced" },
  { name: "github", label: "GitHub", state: "live" },
];

// Each tile's line runs right, joins a shared rail at x=40, then one line to the answer.
const STARTS = [10, 30, 50, 70, 90];
// The tools this answer actually cites; only their lines light up.
const CITED = new Set<BrandName>(["notion", "slack", "linear"]);

/**
 * Hero figure: every tool feeds one answer, and the answer names the three it
 * used. Says "connects your tools" and "cites its sources" in one picture,
 * which is the whole product in a sentence.
 */
export function HeroFigure() {
  return (
    <div className="site-frame hero-fig" aria-hidden>
      <div className="hero-tools">
        {TOOLS.map((t) => (
          <span key={t.name} className={`hero-tool${CITED.has(t.name) ? " is-cited" : ""}`}>
            <BrandGlyph name={t.name} size={20} />
            <b>{t.label}</b>
            <i>{t.state}</i>
          </span>
        ))}
      </div>

      <svg className="hero-wires" viewBox="0 0 100 100" preserveAspectRatio="none">
        {TOOLS.map((t, i) => (
          <path
            key={t.name}
            d={`M0 ${STARTS[i]} H40 V50 H100`}
            className={CITED.has(t.name) ? "is-cited" : ""}
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>

      <div className="hero-answer">
        <div className="hero-answer-head">
          <span className="hero-mark">HB</span>
          <b>Handbook</b>
          <i>Ask</i>
        </div>
        <p className="hero-q">What&rsquo;s still open for the October launch?</p>
        <p className="hero-a">
          Two items: the security review, and the customer walkthrough, which
          moved to 24 October.
        </p>
        <div className="hero-cites">
          <span><BrandGlyph name="linear" size={12} /> LAUNCH-42</span>
          <span><BrandGlyph name="slack" size={12} /> #launch</span>
          <span><BrandGlyph name="notion" size={12} /> Launch plan</span>
        </div>
        <p className="hero-foot">3 sources &middot; only what you can open</p>
      </div>
    </div>
  );
}
