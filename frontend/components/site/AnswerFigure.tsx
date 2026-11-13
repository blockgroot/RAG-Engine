import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { AskIcon, DeepIcon } from "@/components/ModeIcons";

const LINKS: Array<{ name: BrandName; title: string; meta: string }> = [
  { name: "notion", title: "Leave policy", meta: "Notion · People" },
  { name: "drive", title: "Employee handbook 2024.pdf", meta: "Drive · HR" },
  { name: "slack", title: "“does sick leave roll over?”", meta: "Slack · #people-ops" },
  { name: "drive", title: "Leave policy (old draft)", meta: "Drive · Archive" },
  { name: "notion", title: "Onboarding checklist", meta: "Notion · People" },
];

/** The tools Handbook checks, lit one by one before the answer appears. */
const SCAN: BrandName[] = ["notion", "drive", "slack", "linear", "github"];

const STEPS = [
  ["You ask", "Type a question the way you would ask a colleague, in the Handbook chat or by mentioning @Handbook in Slack."],
  ["It searches", "Handbook looks through every tool you connected at once, but only in the documents you are allowed to open."],
  ["It checks", "Before writing anything, it makes sure what it found really answers your question. If nothing comes close, it stops there."],
  ["You get the answer", "A short answer with a link to the document it came from. If your tools don’t hold the answer, it says so instead of guessing."],
];

/** Section 2: one question to a search box (five links) and to Handbook (an
 *  answer), on one 8s loop. Still for reduced motion. */
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
            {LINKS.map((l, i) => (
              <li key={l.title} style={{ ["--i" as string]: i }}>
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
          <div className="duo-scan">
            <span>Checking</span>
            {SCAN.map((t, i) => (
              <i key={t} style={{ ["--i" as string]: i }}><BrandGlyph name={t} size={13} /></i>
            ))}
            <em>1 answers it</em>
          </div>
          <div className="duo-answer">
            <p>
              Full-time employees get <b>10 sick days</b> each calendar year.
              Unused days don&rsquo;t carry over.
            </p>
            <span className="duo-chip">
              <BrandGlyph name="notion" size={12} /> Leave policy &middot; Notion
            </span>
          </div>
        </div>
        <p className="duo-verdict is-ours">&#10003; Answered, with the source linked</p>
      </div>
    </div>
  );
}

/** Two-tone 24px icons, one per step: ask, search the documents, check, answer. */
const ICON = { width: 24, height: 24, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
const STEP_ICONS = [
  <svg key="ask" {...ICON}>
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4h0A1.5 1.5 0 0 1 4 14.5z" fill="currentColor" fillOpacity="0.14" />
    <path d="M8.5 9.5h.01M12 9.5h.01M15.5 9.5h.01" strokeWidth="2.6" />
  </svg>,
  <svg key="search" {...ICON}>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h4" fill="currentColor" fillOpacity="0.14" />
    <path d="M14 3v4a1 1 0 0 0 1 1h4M19 8v2" />
    <path d="M8.5 8h2.5M8.5 11.5h4" />
    <circle cx="16" cy="16" r="3.2" fill="currentColor" fillOpacity="0.14" />
    <path d="m18.4 18.4 2.3 2.3" />
  </svg>,
  <svg key="check" {...ICON}>
    <path d="M12 2.8 19.5 5.6v5.6c0 4.6-3.1 7.9-7.5 9.8-4.4-1.9-7.5-5.2-7.5-9.8V5.6z" fill="currentColor" fillOpacity="0.14" />
    <path d="m8.8 12 2.2 2.2 4.4-4.6" />
  </svg>,
  <svg key="answer" {...ICON}>
    <path d="M12 2.8a6.2 6.2 0 0 0-3.8 11.1c.8.6 1.3 1.5 1.3 2.5v.6h5v-.6c0-1 .5-1.9 1.3-2.5A6.2 6.2 0 0 0 12 2.8z" fill="currentColor" fillOpacity="0.14" />
    <path d="M9.6 20.2h4.8M10.5 22h3" />
    <path d="M12 7.2v3.3M10.4 9h3.2" />
  </svg>,
];

/** How one question moves through Handbook: a light along one line of steps
 *  (CSS only; still for reduced motion). */
export function AskSteps() {
  return (
    <ol className="steps">
      {STEPS.map(([title, body], i) => (
        <li key={title} style={{ ["--i" as string]: i }}>
          <span className="steps-icon" aria-hidden>{STEP_ICONS[i]}</span>
          <small>Step {i + 1}</small>
          <strong>{title}</strong>
          <span>{body}</span>
        </li>
      ))}
    </ol>
  );
}
