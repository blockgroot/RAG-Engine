import type { Metadata } from "next";
import Link from "next/link";

import { BrandGlyph } from "@/components/BrandGlyph";
import { FeatureEmoji } from "@/components/FeatureEmoji";
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
          You ask a question, Handbook looks through the tools your team already
          uses, and you get an answer you can check. Here&rsquo;s what that looks
          like.
        </p>
      </section>

      <div className="how-flow landing-wrap" aria-label="What happens when you ask">
        <article className="how-flow-step" data-reveal style={{ ["--i" as string]: "0" }}>
          <span className="how-flow-num">1</span>
          <strong>You ask</strong>
          <p>In your own words. Follow-ups work too, so you can keep digging.</p>
        </article>
        <span className="how-flow-arrow" aria-hidden />
        <article className="how-flow-step" data-reveal style={{ ["--i" as string]: "1" }}>
          <span className="how-flow-num">2</span>
          <strong>It looks</strong>
          <p>Through everything your company has connected, and nowhere else.</p>
        </article>
        <span className="how-flow-arrow" aria-hidden />
        <article className="how-flow-step" data-reveal style={{ ["--i" as string]: "2" }}>
          <span className="how-flow-num">3</span>
          <strong>It checks</strong>
          <p>If your documents don&rsquo;t cover it, it says so.</p>
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
            Three kinds of answer, one place to ask.
          </h2>
          <p className="show-lead">
            There&rsquo;s nothing to choose from first. Ask for what you want, and
            Handbook works out where to look.
          </p>
        </div>

        <div className="show-tiles">
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-tile-mark">
              <FeatureEmoji name="document" />
            </span>
            <h3>In words</h3>
            <p>
              &ldquo;How much parental leave do I get?&rdquo; gets you a sentence,
              not fourteen search results &mdash; and the document it came from, in
              case you want the detail.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "1" }}>
            <span className="show-tile-mark">
              <FeatureEmoji name="chart" />
            </span>
            <h3>As a chart</h3>
            <p>
              &ldquo;Chart commits by author this quarter.&rdquo; Point at any bar or
              slice and you&rsquo;ll see exactly what it&rsquo;s made of.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-tile-mark">
              <FeatureEmoji name="schedule" />
            </span>
            <h3>Or in your inbox</h3>
            <p>
              Say what you want to keep an eye on and how often, then stop chasing
              it. Every point in the summary links to the real thing.
            </p>
          </article>
        </div>
      </section>

      <section className="show-band show-band-tint" aria-labelledby="trust-title">
        <div className="landing-wrap">
          <div className="show-head" data-reveal>
            <p className="landing-eyebrow">Why you can trust it</p>
            <h2 id="trust-title" className="show-title">
              It would rather say &ldquo;I don&rsquo;t know&rdquo;.
            </h2>
            <p className="show-lead">
              A confident wrong answer about your own company is worse than no answer
              at all. So when Handbook isn&rsquo;t sure, it says so.
            </p>
          </div>

          <ol className="show-flow" aria-label="How answers stay reliable">
            <li className="show-step" data-reveal style={{ ["--i" as string]: "0" }}>
              <span className="show-step-mark">
                <FeatureEmoji name="private" />
              </span>
              <h3>It only reads your own material</h3>
              <p>
                Answers come from what your company connected &mdash; not from the
                open internet, and not from anything it picked up elsewhere.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "1" }}>
              <span className="show-step-mark">
                <FeatureEmoji name="secure" />
              </span>
              <h3>It admits the gaps</h3>
              <p>
                If nothing in your documents covers the question, you&rsquo;ll be
                told plainly &mdash; rather than handed a guess that reads like a
                fact.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "2" }}>
              <span className="show-step-mark">
                <FeatureEmoji name="document" />
              </span>
              <h3>You can check it yourself</h3>
              <p>
                Every answer shows where it came from and links straight to it. You
                never have to take its word for anything.
              </p>
            </li>
          </ol>
        </div>
      </section>

      <section className="show-band landing-wrap" aria-labelledby="setup-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">What you set up</p>
          <h2 id="setup-title" className="show-title">
            Four decisions, then it looks after itself.
          </h2>
          <p className="show-lead">
            Only the first one is required, and none of them need a developer.
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
                Notion, Google Drive, Slack, Linear, GitHub, Google Forms. Someone
                with admin access signs in to each one. Nothing to upload, nothing
                to move.
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
                By email. Everyone signs in with a link we send them, so there&rsquo;s
                no new password for anyone to forget.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-item-mark">
              <FeatureEmoji name="workspace" />
            </span>
            <div>
              <h3>Add spaces, if you need them</h3>
              <p>
                A space gives one team or project its own tools and its own people
                &mdash; useful when not everything should be company-wide.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "3" }}>
            <span className="show-item-mark">
              <FeatureEmoji name="model" />
            </span>
            <div>
              <h3>Bring your own AI, or don&rsquo;t</h3>
              <p>
                Already pay for OpenAI, Anthropic or Google? Add your key and your
                team can pick it. Skip this and Handbook just works.
              </p>
            </div>
          </li>
        </ul>
      </section>

      <section className="show-band landing-wrap" aria-labelledby="privacy-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">Your documents</p>
          <h2 id="privacy-title" className="show-title">
            Read to answer you, and nothing else.
          </h2>
        </div>

        <div className="show-tiles">
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-tile-mark">
              <FeatureEmoji name="private" />
            </span>
            <h3>Never used for training</h3>
            <p>
              Your documents answer your team&rsquo;s questions. They aren&rsquo;t
              used to train AI, and they aren&rsquo;t shared with anyone.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "1" }}>
            <span className="show-tile-mark">
              <FeatureEmoji name="secure" />
            </span>
            <h3>Only your company</h3>
            <p>
              Every question is answered from your own company&rsquo;s material. No
              other company using Handbook can reach it.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-tile-mark">
              <FeatureEmoji name="workspace" />
            </span>
            <h3>You choose who joins</h3>
            <p>
              People join because an admin invited them. Nobody can add themselves,
              and survey results stay with whoever owns the survey.
            </p>
          </article>
        </div>
      </section>

      <section className="how-close landing-wrap" aria-labelledby="how-close">
        <div className="how-close-panel has-spotlight" data-reveal>
          <p className="landing-eyebrow">Ready when you are</p>
          <h2 id="how-close">Start with one tool and one question.</h2>
          <p className="how-close-lead">
            Connect a single tool and ask it something you already know the answer
            to. That&rsquo;s the quickest way to tell whether this is for you.
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
