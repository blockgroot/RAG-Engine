import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

const TOOLS: BrandName[] = ["notion", "drive", "slack", "linear", "github"];

const PEOPLE = [
  { who: "Priya", team: "Finance", sees: ["Q3 budget", "Leave policy", "#finance"] },
  { who: "Sam", team: "Engineering", sees: ["Leave policy", "checkout repo", "PAY-12"] },
];

/**
 * Section 5: the access check as a flow from the tools, through one check
 * per person, to two people who get different documents back. Other
 * companies sit outside the flow entirely.
 */
export function AccessFigure() {
  return (
    <div className="site-frame access" aria-hidden>
      <div className="access-node">
        <span className="access-step">1</span>
        <div>
          <b>Your connected tools</b>
          <div className="access-tools">
            {TOOLS.map((t) => <BrandGlyph key={t} name={t} size={18} />)}
          </div>
          <span className="access-hint">Who can open each item is copied on every sync</span>
        </div>
      </div>

      <div className="access-node access-gate">
        <span className="access-step">2</span>
        <div>
          <b>Access check, for each person</b>
          <span className="access-hint">Runs before the search, so nothing else is ever read</span>
        </div>
      </div>

      <div className="access-people">
        {PEOPLE.map((p) => (
          <div key={p.who} className="access-node access-person">
            <span className="access-avatar">{p.who[0]}</span>
            <div>
              <b>{p.who}</b> <span className="access-hint">{p.team}</span>
              <div className="mode-chips">
                {p.sees.map((d) => <span key={d}>{d}</span>)}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="access-outside">
        <span>Other companies</span>
        <b>Never searched</b>
      </div>
    </div>
  );
}
