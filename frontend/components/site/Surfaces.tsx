import { BrandGlyph } from "@/components/BrandGlyph";

/**
 * Section 4: the three places people meet Handbook. One card each, figure on
 * top and two lines underneath, so the row reads as a set at a glance.
 */
export function Surfaces() {
  return (
    <div className="bento">
      <article className="bento-cell">
        <div className="bento-fig" aria-hidden>
          <div className="slackmini">
            <p className="f-mono"># product-launch</p>
            <div className="slackmini-msg">
              <span className="slackmini-av">SA</span>
              <p><b>Sana</b> <span className="slackmini-at">@Handbook</span> what&rsquo;s still open for launch?</p>
            </div>
            <div className="slackmini-msg">
              <span className="slackmini-av slackmini-bot">HB</span>
              <p>
                <b>Handbook</b> The security review and the customer walkthrough.
                <span className="duo-chip"><BrandGlyph name="notion" size={11} /> Launch plan</span>
              </p>
            </div>
          </div>
        </div>
        <h3>In Slack</h3>
        <p>Mention Handbook in a channel or message it directly. Same answers, same sources, same access rules.</p>
      </article>

      <article className="bento-cell">
        <div className="bento-fig" aria-hidden>
          <div className="report">
            <div className="report-setup">
              <span className="report-tag">Every week</span>
              <p>&ldquo;What did the Platform team ship?&rdquo;</p>
              <span className="report-tools">
                <BrandGlyph name="linear" size={13} />
                <BrandGlyph name="github" size={13} />
              </span>
            </div>
            <span className="report-arrow">&darr; every Monday</span>
            <div className="report-mail">
              <div className="report-mail-head">
                <span className="hero-mark">HB</span>
                <div>
                  <b>Your weekly report is ready</b>
                  <span>Platform team &middot; since last Monday</span>
                </div>
              </div>
              <ul>
                <li>4 issues closed, 2 opened</li>
                <li>Deploy guide updated</li>
              </ul>
              <span className="report-open">Open report &rarr;</span>
            </div>
          </div>
        </div>
        <h3>In your inbox</h3>
        <p>Write what you want to follow and pick daily, weekly or monthly. Handbook reads what changed since the last report and emails you when it&rsquo;s ready.</p>
      </article>

      <article className="bento-cell">
        <div className="bento-fig" aria-hidden>
          <div className="uploads">
            <span className="uploads-file"><b>PDF</b> Vendor contract.pdf</span>
            <span className="uploads-file"><b>XLSX</b> Q3 budget.xlsx</span>
            <span className="uploads-file"><b>CSV</b> survey-export.csv</span>
            <p className="duo-q">Which line items went over budget?</p>
          </div>
        </div>
        <h3>With your own files</h3>
        <p>Drop a PDF, Word, Excel or CSV file into a chat and ask about it, or chart its tables. Only you see it.</p>
      </article>
    </div>
  );
}
