import type { Metadata } from "next";
import Link from "next/link";

import { BrandGlyph } from "@/components/BrandGlyph";
import { LandingShell } from "@/components/LandingShell";
import { RevealOnScroll } from "@/components/Reveal";

export const metadata: Metadata = {
  title: "How it works",
  description:
    "How Handbook answers a work question from the tools your team already uses.",
};

/**
 * The public walkthrough.
 *
 * Written for someone deciding whether to use this, not for someone
 * maintaining it: what they can ask, what arrives on its own, what they set
 * up, and what happens to their content. The mechanics behind each of those
 * are real and documented in CLAUDE.md — they are not what a visitor is here
 * to read, and naming them made this page read like a design doc.
 */
export default function HowItWorksPage() {
  return (
    <LandingShell active="how">
      <RevealOnScroll />

      <section className="how-hero landing-wrap" aria-labelledby="how-title">
        <p className="landing-eyebrow" data-reveal>
          How it works
        </p>
        <h1 id="how-title" className="how-title" data-reveal style={{ ["--i" as string]: "1" }}>
          Ask a question.
          <span className="show-title-accent"> Get your answer.</span>
        </h1>
        <p className="how-lead" data-reveal style={{ ["--i" as string]: "2" }}>
          Handbook reads the tools your team already uses and answers from what is
          in them. Four steps, and only the first one is yours.
        </p>
      </section>

      <div className="how-flow landing-wrap" aria-label="What happens when you ask">
        <article className="how-flow-step" data-reveal style={{ ["--i" as string]: "0" }}>
          <span className="how-flow-num">1</span>
          <strong>You ask</strong>
          <p>In your own words. Follow-up questions keep the thread.</p>
        </article>
        <span className="how-flow-arrow" aria-hidden />
        <article className="how-flow-step" data-reveal style={{ ["--i" as string]: "1" }}>
          <span className="how-flow-num">2</span>
          <strong>It looks</strong>
          <p>Across everything your company has connected, and nowhere else.</p>
        </article>
        <span className="how-flow-arrow" aria-hidden />
        <article className="how-flow-step" data-reveal style={{ ["--i" as string]: "2" }}>
          <span className="how-flow-num">3</span>
          <strong>It checks</strong>
          <p>If your content doesn&rsquo;t cover the question, it says so.</p>
        </article>
        <span className="how-flow-arrow" aria-hidden />
        <article className="how-flow-step" data-reveal style={{ ["--i" as string]: "3" }}>
          <span className="how-flow-num">4</span>
          <strong>You get it</strong>
          <p>With a link to the page, message or file it came from.</p>
        </article>
      </div>

      <section className="show-band landing-wrap" aria-labelledby="ask-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">What you can ask for</p>
          <h2 id="ask-title" className="show-title">
            Three kinds of answer, one question box.
          </h2>
          <p className="show-lead">
            You never pick a mode or a source. Ask for what you want and Handbook
            works out where it lives.
          </p>
        </div>

        <div className="show-tiles">
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-tile-mark">
              <BrandGlyph name="document" size={26} />
            </span>
            <h3>An answer, in words</h3>
            <p>
              &ldquo;How much parental leave do I get?&rdquo; comes back as a
              sentence, with the document it came from and who last edited it.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "1" }}>
            <span className="show-tile-mark">
              <BrandGlyph name="chart" size={26} />
            </span>
            <h3>A chart, from real figures</h3>
            <p>
              &ldquo;Chart commits by author this quarter.&rdquo; Hover any bar or
              slice to see exactly what it counted, down to the day.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-tile-mark">
              <BrandGlyph name="schedule" size={26} />
            </span>
            <h3>A report, on repeat</h3>
            <p>
              Describe what to keep an eye on, choose how often, and read it in
              your inbox with links to everything behind it.
            </p>
          </article>
        </div>
      </section>

      <section className="show-band show-band-tint" aria-labelledby="trust-title">
        <div className="landing-wrap">
          <div className="show-head" data-reveal>
            <p className="landing-eyebrow">Why you can rely on it</p>
            <h2 id="trust-title" className="show-title">
              It would rather say &ldquo;I don&rsquo;t know&rdquo;.
            </h2>
            <p className="show-lead">
              A confident wrong answer about your own company is worse than no
              answer. Three things make sure you get the second one.
            </p>
          </div>

          <ol className="show-flow" aria-label="How answers stay reliable">
            <li className="show-step" data-reveal style={{ ["--i" as string]: "0" }}>
              <span className="show-step-mark">
                <BrandGlyph name="private" size={22} />
              </span>
              <h3>Only your content</h3>
              <p>
                Answers are built from what your company connected &mdash; not from
                what a model happens to have read.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "1" }}>
              <span className="show-step-mark">
                <BrandGlyph name="secure" size={22} />
              </span>
              <h3>It admits the gaps</h3>
              <p>
                When your documents don&rsquo;t answer something, you get a clear
                &ldquo;not covered&rdquo; instead of a guess that sounds right.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "2" }}>
              <span className="show-step-mark">
                <BrandGlyph name="document" size={22} />
              </span>
              <h3>You can check it</h3>
              <p>
                Every answer names its source and links to it, so trusting it is a
                click rather than a leap.
              </p>
            </li>
          </ol>
        </div>
      </section>

      <section className="show-band landing-wrap" aria-labelledby="setup-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">What you set up</p>
          <h2 id="setup-title" className="show-title">
            Four decisions, then it runs itself.
          </h2>
          <p className="show-lead">
            All of it is optional except the first, and none of it needs a
            developer.
          </p>
        </div>

        <ul className="show-list">
          <li className="show-item" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-item-mark">
              <BrandGlyph name="notion" size={24} />
            </span>
            <div>
              <h3>Connect your tools</h3>
              <p>
                Notion, Google Drive, Slack, Linear, GitHub, Google Forms. An admin
                signs in to each one; nothing needs to be uploaded or copied.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "1" }}>
            <span className="show-item-mark">
              <BrandGlyph name="sendgrid" size={24} />
            </span>
            <div>
              <h3>Invite your team</h3>
              <p>
                By email. Everyone signs in with a link, so there is no new
                password for anyone to forget.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-item-mark">
              <BrandGlyph name="workspace" size={24} />
            </span>
            <div>
              <h3>Create spaces, if you want them</h3>
              <p>
                A space gives one project or team its own sources and its own
                people. What belongs to a space stays in it.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "3" }}>
            <span className="show-item-mark">
              <BrandGlyph name="model" size={24} />
            </span>
            <div>
              <h3>Bring your own AI model</h3>
              <p>
                Add a key from OpenAI, Anthropic, Google or another provider and
                your team can choose it. Leave it alone and Handbook uses ours.
              </p>
            </div>
          </li>
        </ul>
      </section>

      <section className="show-band landing-wrap" aria-labelledby="privacy-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">Your content</p>
          <h2 id="privacy-title" className="show-title">
            Read to answer you. Nothing else.
          </h2>
        </div>

        <div className="show-tiles">
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-tile-mark">
              <BrandGlyph name="private" size={26} />
            </span>
            <h3>Never used for training</h3>
            <p>
              Your documents are read to answer your team&rsquo;s questions. They
              are not used to train AI models and are not shared with anyone else.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "1" }}>
            <span className="show-tile-mark">
              <BrandGlyph name="secure" size={26} />
            </span>
            <h3>Only your company</h3>
            <p>
              Every question is answered from your own company&rsquo;s content.
              Another company on Handbook can never reach it.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-tile-mark">
              <BrandGlyph name="workspace" size={26} />
            </span>
            <h3>You choose who joins</h3>
            <p>
              Admins invite people by email. Nobody can sign themselves into your
              company, and a survey&rsquo;s results stay with its owners.
            </p>
          </article>
        </div>
      </section>

      <section className="how-close landing-wrap" aria-labelledby="how-close">
        <div className="how-close-panel has-spotlight" data-reveal>
          <p className="landing-eyebrow">Ready when you are</p>
          <h2 id="how-close">Start with one tool and one question.</h2>
          <p className="how-close-lead">
            Connect a single source and ask something you already know the answer
            to. That is the fastest way to see whether this belongs in your week.
          </p>
          <div className="how-close-actions">
            <Link href="/signup" className="button landing-cta-primary">
              Request access
            </Link>
            <Link href="/" className="landing-close-link">
              Back to the overview
            </Link>
          </div>
        </div>
      </section>
    </LandingShell>
  );
}
