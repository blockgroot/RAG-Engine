import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { HbLogo } from "@/components/site/HbLogo";

/** How each tool reaches Handbook; mirrors app/jobs/autosync.py. */
const FRESH: Array<[BrandName, string, boolean]> = [
  ["slack", "Slack", false],
  ["notion", "Notion", false],
  ["linear", "Linear", false],
  ["drive", "Drive", false],
  ["github", "GitHub", true],
];

/** Three ways of asking one thing: grouped by meaning into one topic. */
const ASKS: Array<[string, string]> = [
  ["A", "What’s the travel expense limit?"],
  ["R", "How much can I claim for hotels?"],
  ["K", "Is there a cap on travel costs?"],
];

/**
 * Section 7: what keeps happening without anyone asking. Both pictures move:
 * changes travel from each tool into Handbook, and unanswered questions slide
 * together into one flagged topic. Reduced motion shows the end state, still.
 */
export function Background() {
  return (
    <div className="bento is-two">
      <article className="bento-cell">
        <div className="bento-fig" aria-hidden>
          <div className="bgfig sync">
            <div className="sync-tools">
              {FRESH.map(([name, label, live], i) => (
                <span key={name} className={live ? "is-live" : ""} style={{ ["--i" as string]: i }}>
                  <b className="sync-logo"><BrandGlyph name={name} size={18} /></b>
                  <small>{label}</small>
                  <i className="sync-wire"><em /></i>
                </span>
              ))}
            </div>
            <div className="sync-card">
              <div className="sync-card-head">
                <HbLogo />
                <b>Handbook</b>
                <small><i className="sync-pulse" /> up to date</small>
              </div>
              <div className="sync-feed">
                <span className="is-new"><BrandGlyph name="notion" size={12} /> Q4 pricing <em>updated just now</em></span>
                <span><BrandGlyph name="linear" size={12} /> PAY-12 &middot; US cards <em>2 min ago</em></span>
                <span><BrandGlyph name="github" size={12} /> checkout#218 <em>read live</em></span>
              </div>
            </div>
            <p className="bgfig-note sync-note"><i className="sync-clock" /> An hourly check catches anything missed</p>
          </div>
        </div>
        <h3>Always current</h3>
        <p>
          Slack, Notion, Linear and Drive tell Handbook when something changes.
          GitHub is read live when you ask, and an hourly check fills any gap.
        </p>
      </article>

      <article className="bento-cell">
        <div className="bento-fig" aria-hidden>
          <div className="bgfig gaps">
            <div className="gaps-asks">
              {ASKS.map(([who, q], i) => (
                <span key={who} style={{ ["--i" as string]: i }}>
                  <b className={`gaps-av is-${i}`}>{who}</b>
                  &ldquo;{q}&rdquo;
                  <em>no answer</em>
                </span>
              ))}
            </div>
            <div className="gaps-topic">
              <div>
                <small>Topic &middot; grouped by meaning</small>
                <b>Travel expense limit</b>
                <span className="gaps-people">
                  <i className="gaps-av is-0">A</i><i className="gaps-av is-1">R</i><i className="gaps-av is-2">K</i>
                  3 people couldn&rsquo;t get an answer
                </span>
              </div>
              <em>Flagged for the admin</em>
            </div>
          </div>
        </div>
        <h3>Knowledge gaps</h3>
        <p>
          Questions nobody could answer are grouped by topic and flagged for
          the admin, or the space owner, who can add the missing document.
        </p>
      </article>
    </div>
  );
}
