import { BrandGlyph } from "@/components/BrandGlyph";
import { HbLogo } from "@/components/site/HbLogo";

/**
 * Section 4: the three places people meet Handbook. One card each, figure on
 * top and two lines underneath, so the row reads as a set at a glance.
 */
export function Surfaces() {
  return (
    <div className="bento">
      <article className="bento-cell">
        <div className="bento-fig" aria-hidden>
          <div className="surf">
            <p className="surf-head"><BrandGlyph name="slack" size={13} /> # product-launch</p>
            <div className="surf-msg">
              <span className="surf-av">SA</span>
              <p><b>Sana</b><span className="surf-at">@Handbook</span> what&rsquo;s still open for launch?</p>
            </div>
            <div className="surf-msg">
              <HbLogo className="surf-av" />
              <p><b>Handbook</b>The security review and the customer walkthrough.</p>
            </div>
            <p className="surf-src"><BrandGlyph name="notion" size={11} /> Launch plan</p>
          </div>
        </div>
        <h3>In Slack</h3>
        <p>Mention Handbook in a channel or message it directly. Same answers, same sources, same access rules.</p>
      </article>

      <article className="bento-cell">
        <div className="bento-fig" aria-hidden>
          <div className="surf">
            <p className="surf-head"><MailIcon /> Inbox</p>
            <div className="surf-mail is-new">
              <HbLogo className="surf-av" />
              <p><b>Weekly report &middot; Platform team</b><span>4 issues closed, deploy guide updated</span></p>
              <small>Mon</small>
            </div>
            <div className="surf-mail">
              <HbLogo className="surf-av" />
              <p><b>Weekly report &middot; Platform team</b><span>6 issues closed, 1 release</span></p>
              <small>Mon</small>
            </div>
            <p className="surf-src">Every Monday, from <BrandGlyph name="linear" size={11} /> <BrandGlyph name="github" size={11} /></p>
          </div>
        </div>
        <h3>In your inbox</h3>
        <p>Write what you want to follow and pick daily, weekly or monthly. Handbook reads what changed since the last report and emails you when it&rsquo;s ready.</p>
      </article>

      <article className="bento-cell">
        <div className="bento-fig" aria-hidden>
          <div className="surf">
            <p className="surf-head"><HbLogo className="surf-chat" /> Chat</p>
            <div className="surf-file">
              <b>XLSX</b>
              <p>Q3 budget.xlsx<span>3 sheets &middot; only you can see it</span></p>
            </div>
            <p className="surf-q">Which line items went over budget?</p>
          </div>
        </div>
        <h3>With your own files</h3>
        <p>Drop a PDF, Word, Excel or CSV file into a chat and ask about it, or chart its tables. Only you see it.</p>
      </article>
    </div>
  );
}

function MailIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">
      <rect x="2" y="3.5" width="12" height="9" rx="1.5" />
      <path d="M2.5 4.5 8 9l5.5-4.5" />
    </svg>
  );
}
