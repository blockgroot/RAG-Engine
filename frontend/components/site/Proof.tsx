/**
 * Section 8. Every number is copied from docs/benchmarks/benchmark-2-results.md
 * (same AI model for both, same 150 EnterpriseRAG-Bench questions, graded
 * blind). Re-run the benchmark before changing one. The other system is not
 * named: this page names no company or model, ours or anyone else's.
 */
const SCORES: Array<{ label: string; note: string; ours: number; theirs: number; shown: [string, string] }> = [
  {
    label: "Said “I don’t know” when the answer wasn’t there",
    note: "15 questions with no answer in the documents",
    ours: 100,
    theirs: 6.7,
    shown: ["15 of 15", "1 of 15"],
  },
  {
    label: "Answers with a made-up claim",
    note: "Lower is better",
    ours: 10.7,
    theirs: 47.3,
    shown: ["10.7%", "47.3%"],
  },
  {
    label: "Right answer, or an honest “I don’t know”",
    note: "All 150 questions",
    ours: 43.3,
    theirs: 34.7,
    shown: ["43.3%", "34.7%"],
  },
  {
    label: "Got the key answer right",
    note: "Even",
    ours: 65.3,
    theirs: 64.7,
    shown: ["65.3%", "64.7%"],
  },
];

/** Per-question averages from the same run. One dot = 1,000 tokens of text read. */
const COST = [
  { who: "Handbook", tokens: 4215, calls: "3.6", seconds: 6.2, ours: true },
  { who: "Another assistant", tokens: 69466, calls: "12.1", seconds: 11.2, ours: false },
];

export function Scoreboard() {
  return (
    <figure className="site-frame score">
      <div className="f-card score-card">
        <div className="score-head">
          <span>Head to head &middot; same model, graded blind</span>
          <span>150 questions</span>
        </div>
        <div className="score-legend" aria-hidden>
          <span><i className="score-key-ours" /> Handbook</span>
          <span><i className="score-key-theirs" /> Another assistant</span>
        </div>
        <ul className="score-rows">
          {SCORES.map((s) => (
            <li key={s.label}>
              <strong>{s.label}</strong>
              <span className="f-mono">{s.note}</span>
              <div className="score-bar score-bar-ours" style={{ ["--w" as string]: `${s.ours}%` }}>
                <span className="sr-only">Handbook </span><b>{s.shown[0]}</b>
              </div>
              <div className="score-bar score-bar-theirs" style={{ ["--w" as string]: `${s.theirs}%` }}>
                <span className="sr-only">Another assistant </span><b>{s.shown[1]}</b>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </figure>
  );
}

/**
 * How much each assistant had to read for one answer, drawn as dots so the
 * gap is seen before it is read, plus time to the first word.
 */
export function Receipts() {
  const slowest = Math.max(...COST.map((c) => c.seconds));
  return (
    <div className="site-frame cost">
      <div className="f-card cost-card">
        <p className="cost-title">Text read to answer one question</p>
        {COST.map((c) => (
          <div key={c.who} className={`cost-row${c.ours ? " is-ours" : ""}`}>
            <div className="cost-who">
              <b>{c.who}</b>
              <span>{c.tokens.toLocaleString("en-US")} tokens &middot; {c.calls} AI calls</span>
            </div>
            <div className="cost-dots" aria-hidden>
              {Array.from({ length: Math.round(c.tokens / 1000) }, (_, i) => <i key={i} />)}
            </div>
          </div>
        ))}
        <p className="cost-key">One dot is about 1,000 tokens</p>

        <p className="cost-title">Time to the first word</p>
        {COST.map((c) => (
          <div key={c.who} className={`cost-time${c.ours ? " is-ours" : ""}`}>
            <span>{c.who}</span>
            <i style={{ ["--w" as string]: `${(c.seconds / slowest) * 100}%` }} />
            <b>{c.seconds} s</b>
          </div>
        ))}
      </div>
    </div>
  );
}
