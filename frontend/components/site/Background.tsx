import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

/** How each tool reaches Handbook; mirrors app/jobs/autosync.py. */
const FRESH: Array<[BrandName, string, boolean]> = [
  ["slack", "Slack", false],
  ["notion", "Notion", false],
  ["linear", "Linear", false],
  ["drive", "Drive", false],
  ["github", "GitHub", true],
];

/**
 * Survey bars: five labels per question, stacked, never averaged into one
 * score (app/insights/sentiment.py). Illustrative shares, in percent.
 */
const SURVEY: Array<[string, number[]]> = [
  ["Onboarding", [4, 8, 18, 44, 26]],
  ["Internal tools", [10, 22, 30, 28, 10]],
  ["On-call", [24, 34, 22, 14, 6]],
];

/**
 * Section 7: what keeps happening without anyone asking. Same card shape as
 * "Where you use it": a small picture of the behaviour, then two lines.
 */
export function Background() {
  return (
    <div className="bento is-two">
      <article className="bento-cell">
        <div className="bento-fig" aria-hidden>
          <div className="bgfig">
            <div className="bgfig-tools">
              {FRESH.map(([name, label, live]) => (
                <span key={name} className={live ? "is-live" : ""}>
                  <BrandGlyph name={name} size={16} />
                  <b>{label}</b>
                  <i>{live ? "read live" : "notifies"}</i>
                </span>
              ))}
            </div>
            <div className="bgfig-arrow">
              <span>changes arrive within minutes</span>
            </div>
            <div className="bgfig-row is-accent">
              <span className="hero-mark">HB</span>
              <b>Answers use the latest version</b>
            </div>
            <p className="bgfig-note">+ an hourly check catches anything missed</p>
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
          <div className="bgfig">
            <div className="bgfig-asks">
              <span>&ldquo;What&rsquo;s the travel expense limit?&rdquo;</span>
              <span>&ldquo;How much can I claim for hotels?&rdquo;</span>
              <span>&ldquo;Is there a cap on travel costs?&rdquo;</span>
            </div>
            <div className="bgfig-arrow"><span>grouped by meaning</span></div>
            <div className="bgfig-row">
              <div>
                <b>&ldquo;What&rsquo;s the travel expense limit?&rdquo;</b>
                <span>3 people couldn&rsquo;t get an answer</span>
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

      <article className="bento-cell">
        <div className="bento-fig" aria-hidden>
          <div className="bgfig">
            <div className="bgfig-survey">
              {SURVEY.map(([topic, parts]) => (
                <div key={topic}>
                  <span>{topic}</span>
                  <div className="bgfig-stack">
                    {parts.map((w, i) => (
                      <i key={i} className={`s${i}`} style={{ width: `${w}%` }} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <div className="bgfig-legend">
              <span><i className="s0" /> negative</span>
              <span><i className="s2" /> neutral</span>
              <span><i className="s4" /> positive</span>
            </div>
            <p className="bgfig-note">From a Google Form &middot; no names, no answers kept</p>
          </div>
        </div>
        <h3>Survey insights</h3>
        <p>
          Connect a Google Form to see how your team feels about each topic.
          Only the feeling is kept, never what someone wrote.
        </p>
      </article>

      <article className="bento-cell">
        <div className="bento-fig" aria-hidden>
          <div className="bgfig bgfig-chats">
            <p className="bgfig-head">Your chats <span>only you</span></p>
            <div className="bgfig-chat is-on">
              <b>October launch</b>
              <span><BrandGlyph name="notion" size={11} /> 3 sources</span>
            </div>
            <div className="bgfig-chat">
              <b>Commits by author</b>
              <span>chart</span>
            </div>
            <div className="bgfig-chat">
              <b>Leave policy</b>
              <span><BrandGlyph name="notion" size={11} /> 1 source</span>
            </div>
          </div>
        </div>
        <h3>Chats you can come back to</h3>
        <p>
          Your conversations are private to you and keep their charts and
          sources, exactly as they were, when you open them again.
        </p>
      </article>
    </div>
  );
}
