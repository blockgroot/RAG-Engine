import type { Metadata } from "next";
import Link from "next/link";

import { BrandGlyph } from "@/components/BrandGlyph";
import { AnswerArt, ChartArt, InboxArt } from "@/components/HowAnswerArt";
import { HowJourney } from "@/components/HowJourney";
import { LandingShell } from "@/components/LandingShell";
import { RevealOnScroll, ScrollRail } from "@/components/Reveal";

export const metadata: Metadata = {
  title: "How it works",
  description:
    "How Handbook answers a work question from the tools your team already uses.",
};

/**
 * The public walkthrough.
 *
 * Copy rule, same as the landing page: describe the outcome, never the
 * machinery, in the words a visitor would use. Layout rule, learned the hard
 * way here: a page of bordered cards reads as filler no matter how good the
 * copy is, so each section has a different shape and only the closing panel is
 * boxed. Every claim that can be SHOWN is shown — an answer with its source, a
 * chart of real bars, a schedule ticking — because three sentences about a
 * chart is not a chart.
 */
export default function HowItWorksPage() {
  return (
    <LandingShell active="how">
      <RevealOnScroll />
      <ScrollRail />

      <section className="how-hero landing-wrap has-spotlight" aria-labelledby="how-title">
        <span className="how-hero-aura" aria-hidden />
        <p className="landing-eyebrow" data-reveal>
          How it works
        </p>
        <h1 id="how-title" className="how-title" data-reveal style={{ ["--i" as string]: "1" }}>
          Ask a question.
          <span className="show-title-accent"> Get your answer.</span>
        </h1>
        <p className="how-lead" data-reveal style={{ ["--i" as string]: "2" }}>
          You ask, Handbook looks through the tools your team already uses, and you
          get an answer you can check. Here&rsquo;s what that looks like.
        </p>
        {/* The hero was three lines of text and a lot of nothing. This says
            which tools, in the one place a visitor asks it. */}
        <div className="how-hero-tools" data-reveal style={{ ["--i" as string]: "3" }}>
          <span className="how-hero-tools-label">Works with</span>
          <span className="how-hero-tools-row">
            {(["notion", "drive", "slack", "linear", "github"] as const).map((name, i) => (
              <span
                key={name}
                className="how-hero-tool"
                style={{ ["--f" as string]: String(i) }}
              >
                <BrandGlyph name={name} size={22} />
              </span>
            ))}
          </span>
        </div>
      </section>

      <div className="landing-wrap">
        <HowJourney />
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

        <div className="feat" data-reveal>
          <div className="feat-copy">
            <span className="feat-kicker">In words</span>
            <h3>A straight answer, with its receipt</h3>
            <p>
              &ldquo;How much parental leave do I get?&rdquo; gets you a sentence,
              not fourteen search results. The document it came from sits right
              underneath, in case you want the detail.
            </p>
          </div>
          <div className="feat-art">
            <AnswerArt />
          </div>
        </div>

        <div className="feat feat-flip" data-reveal>
          <div className="feat-copy">
            <span className="feat-kicker">As a chart</span>
            <h3>Real figures, not a picture of some</h3>
            <p>
              &ldquo;Chart commits by author this quarter.&rdquo; Point at any bar
              and you&rsquo;ll see what it&rsquo;s made of &mdash; who did what, and
              when.
            </p>
          </div>
          <div className="feat-art">
            <ChartArt />
          </div>
        </div>

        <div className="feat" data-reveal>
          <div className="feat-copy">
            <span className="feat-kicker">Or in your inbox</span>
            <h3>The update that arrives without asking</h3>
            <p>
              Say what you want to keep an eye on and how often, then stop chasing
              it. Every point in the summary links to the real thing.
            </p>
          </div>
          <div className="feat-art">
            <InboxArt />
          </div>
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
                <BrandGlyph name="private" size={22} />
              </span>
              <h3>It only reads your own material</h3>
              <p>
                Answers come from what your company connected &mdash; not from the
                open internet, and not from anything it picked up elsewhere.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "1" }}>
              <span className="show-step-mark">
                <BrandGlyph name="secure" size={22} />
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
                <BrandGlyph name="document" size={22} />
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
                By email. Everyone signs in with a link we send them, so
                there&rsquo;s no new password for anyone to forget.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-item-mark">
              <BrandGlyph name="workspace" size={24} />
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
              <BrandGlyph name="model" size={24} />
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

      {/* A statement, not a third grid. Three short guarantees on one line read
          as one promise; the same three as tiles read as more cards to scan. */}
      <section className="show-band vow landing-wrap" aria-labelledby="privacy-title">
        <span className="vow-aura" aria-hidden />
        <p className="landing-eyebrow" data-reveal>
          Your documents
        </p>
        <h2 id="privacy-title" className="vow-title" data-reveal style={{ ["--i" as string]: "1" }}>
          Read to answer you,
          <span className="show-title-accent"> and nothing else.</span>
        </h2>
        <ul className="vow-points">
          <li data-reveal style={{ ["--i" as string]: "2" }}>
            <strong>Never used for training.</strong> Your documents answer your
            team&rsquo;s questions. They aren&rsquo;t used to train AI, and they
            aren&rsquo;t shared with anyone.
          </li>
          <li data-reveal style={{ ["--i" as string]: "3" }}>
            <strong>Only your company.</strong> Every question is answered from your
            own company&rsquo;s material. No other company using Handbook can reach
            it.
          </li>
          <li data-reveal style={{ ["--i" as string]: "4" }}>
            <strong>You choose who joins.</strong> People join because an admin
            invited them. Nobody can add themselves, and survey results stay with
            whoever owns the survey.
          </li>
        </ul>
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
