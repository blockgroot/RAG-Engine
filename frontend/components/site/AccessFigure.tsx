import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

const DOCS: Array<[BrandName, string]> = [
  ["drive", "Q3 budget"],
  ["notion", "Leave policy"],
  ["slack", "#finance"],
  ["github", "checkout repo"],
  ["linear", "PAY-12"],
];

/** What each person gets back from the same five documents: true = can open. */
const PEOPLE: Array<{ who: string; team: string; tone: string; open: boolean[] }> = [
  { who: "Priya", team: "Finance", tone: "is-a", open: [true, true, true, false, false] },
  { who: "Sam", team: "Engineering", tone: "is-b", open: [false, true, false, true, true] },
];

const Lock = () => (
  <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
    <rect x="3.5" y="7" width="9" height="6.5" rx="1.5" />
    <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" />
  </svg>
);
const Tick = () => (
  <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3.5 8.5l3 3 6-7" />
  </svg>
);

/**
 * Section 5: the same documents pass through one access check per person.
 * Each person's list shows what got through (ticked) and what was held back
 * (locked), so two colleagues visibly get different answers. A light sweeps
 * the gate on a loop (still for reduced motion). Other companies never enter.
 */
export function AccessFigure() {
  return (
    <div className="site-frame acx" aria-hidden>
      <div className="acx-docs">
        <small>Your connected tools</small>
        <div>
          {DOCS.map(([t, d]) => (
            <span key={d}><BrandGlyph name={t} size={12} />{d}</span>
          ))}
        </div>
      </div>

      <div className="acx-gate">
        <span className="acx-gate-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2.8 19.5 5.6v5.6c0 4.6-3.1 7.9-7.5 9.8-4.4-1.9-7.5-5.2-7.5-9.8V5.6z" />
            <path d="m8.8 12 2.2 2.2 4.4-4.6" />
          </svg>
        </span>
        <div>
          <b>Access check, for each person</b>
          <small>Sharing copied from each tool, checked before any search</small>
        </div>
      </div>

      <div className="acx-people">
        {PEOPLE.map((p) => (
          <div key={p.who} className="acx-person">
            <div className="acx-who">
              <span className={`acx-avatar ${p.tone}`}>{p.who[0]}</span>
              <b>{p.who}</b>
              <small>{p.team}</small>
            </div>
            <ul>
              {DOCS.map(([t, d], i) => (
                <li key={d} className={p.open[i] ? "is-open" : "is-locked"} style={{ ["--i" as string]: i }}>
                  <BrandGlyph name={t} size={11} />
                  <span>{d}</span>
                  {p.open[i] ? <Tick /> : <Lock />}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <p className="acx-outside"><Lock /> Other companies&rsquo; documents are never searched</p>
    </div>
  );
}
