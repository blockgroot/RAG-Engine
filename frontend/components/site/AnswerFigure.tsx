import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

const LINKS: Array<{ name: BrandName; title: string; meta: string }> = [
  { name: "notion", title: "Leave policy", meta: "Notion · People" },
  { name: "drive", title: "Employee handbook 2024.pdf", meta: "Drive · HR" },
  { name: "slack", title: "“does sick leave roll over?”", meta: "Slack · #people-ops" },
  { name: "drive", title: "Leave policy (old draft)", meta: "Drive · Archive" },
  { name: "notion", title: "Onboarding checklist", meta: "Notion · People" },
];

const STEPS = [
  ["You ask", "Type a question the way you would ask a colleague, in the Handbook chat or by mentioning @Handbook in Slack."],
  ["It searches", "Handbook looks through every tool you connected at once, but only in the documents you are allowed to open."],
  ["It checks", "Before writing anything, it makes sure what it found really answers your question. If nothing comes close, it stops there."],
  ["You get the answer", "A short answer with a link to the document it came from. If your tools don’t hold the answer, it says so instead of guessing."],
];

/**
 * Section 2: the same question asked of a search box and of Handbook. The
 * search side is not wrong, it is just work: five links and no answer.
 */
export function AnswerFigure() {
  return (
    <div className="site-frame duo" aria-hidden>
      <div className="duo-side">
        <span className="duo-tag">
          <b>Search</b> a list of links
        </span>
        <div className="duo-card">
          <p className="duo-q">How many sick days do full-time employees get?</p>
          <ul className="duo-links">
            {LINKS.map((l) => (
              <li key={l.title}>
                <BrandGlyph name={l.name} size={14} />
                <span>{l.title}</span>
                <i>{l.meta}</i>
              </li>
            ))}
          </ul>
        </div>
        <p className="duo-verdict">5 links to open &middot; still no answer</p>
      </div>

      <div className="duo-side">
        <span className="duo-tag duo-tag-ours">
          <b>Handbook</b> one answer
        </span>
        <div className="duo-card">
          <p className="duo-q">How many sick days do full-time employees get?</p>
          <div className="duo-answer">
            <p>
              Full-time employees get <b>10 sick days</b> each calendar year.
              Unused days don&rsquo;t carry over.
            </p>
            <span className="duo-chip">
              <BrandGlyph name="notion" size={12} /> Leave policy &middot; Notion
            </span>
          </div>
          <p className="duo-note">
            searched 5 tools &middot; checked 5 documents &middot; 1 answers it
          </p>
        </div>
        <p className="duo-verdict is-ours">&#10003; Answered, with the source linked</p>
      </div>
    </div>
  );
}

export function AskSteps() {
  return (
    <ol className="steps">
      {STEPS.map(([title, body], i) => (
        <li key={title}>
          <span className="steps-n">0{i + 1}</span>
          <strong>{title}</strong>
          <span>{body}</span>
        </li>
      ))}
    </ol>
  );
}
