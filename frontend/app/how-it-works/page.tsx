import type { Metadata } from "next";
import Link from "next/link";

import { BrandGlyph } from "@/components/BrandGlyph";
import { BrandMark } from "@/components/BrandMark";
import { AnswerArt, ChartArt, InboxArt } from "@/components/HowAnswerArt";
import { HowJourney } from "@/components/HowJourney";
import { HowSetupTracker } from "@/components/HowSetupTracker";
import { LandingShell } from "@/components/LandingShell";
import { ScrollRail } from "@/components/Reveal";

export const metadata: Metadata = {
  title: "How it works",
  description:
    "What Handbook does with your connected tools, what you can ask it for, and what you need to set up.",
};

/**
 * The explanatory page. It carries the DEFINITIONS and the detail; the landing
 * page carries the short version and must not repeat these paragraphs.
 *
 * Two standing rules for the copy here:
 *  - Plain, complete sentences that explain a function. No aphorisms, no
 *    winks, no "not fourteen search results" — a reader on this page is trying
 *    to understand what the product does, and a joke is a sentence that does
 *    not answer them.
 *  - Describe the outcome, never the machinery. "Grounded", "org-scoped" and
 *    "the confidence gate" are real and belong in CLAUDE.md.
 */
export default function HowItWorksPage() {
  return (
    <LandingShell active="how">
      <ScrollRail />

      <section className="how-hero landing-wrap has-spotlight" aria-labelledby="how-title">
        <span className="how-hero-aura" aria-hidden />
        <div className="how-hero-copy">
          <p className="landing-eyebrow">How it works</p>
          <h1 id="how-title" className="how-title">
            From your question to a useful answer.
          </h1>
          <p className="how-lead">
            Handbook connects to the tools your team already uses. Ask in plain
            English, see the answer, and open the original document when you need
            more detail.
          </p>
          <p className="how-hero-tools-label">Works with</p>
        </div>

        {/* The right half was empty and the tools sat in a flat row under the
            copy. They are the subject of the sentence above, so they carry the
            column: five marks on a slow ring around Handbook, each staying
            upright as it travels and lifting under the cursor. CSS only — the
            page is otherwise static, and this does not need to know the
            scroll position. */}
        <div className="how-orbit" aria-hidden>
          <span className="how-orbit-ring" />
          <span className="how-orbit-core">
            <BrandMark className="brand-mark how-orbit-mark" />
          </span>
          <ul className="how-orbit-set">
            {(["notion", "drive", "slack", "linear", "github"] as const).map((name, i) => (
              <li
                key={name}
                className="how-orbit-item"
                style={{ ["--a" as string]: `${i * 72}deg` }}
              >
                <span className="how-orbit-upright">
                  <span className="how-orbit-tile">
                    <BrandGlyph name={name} size={26} />
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="show-band landing-wrap" aria-labelledby="journey-title">
        <div className="show-head" data-reveal>
            <p className="landing-eyebrow">A simple process</p>
          <h2 id="journey-title" className="show-title">
            What happens after you ask.
          </h2>
          <p className="show-lead">
            You ask the question. Handbook takes care of the rest.
          </p>
        </div>
        <HowJourney />
      </section>

      <section className="show-band show-band-tint" aria-labelledby="ask-title">
        <div className="landing-wrap">
          <div className="show-head" data-reveal>
            <p className="landing-eyebrow">What you get back</p>
            <h2 id="ask-title" className="show-title">
              The right format for your request.
            </h2>
            <p className="show-lead">
              Ask naturally. Handbook gives you a written answer, a chart or a report
              based on what you need.
            </p>
          </div>

          <div className="feat" data-reveal>
            <div className="feat-copy">
              <h3>Written answers</h3>
              <p>
                Ask, &ldquo;What&rsquo;s the status of ENG-142?&rdquo; and get a short answer
                from the ticket, page or conversation that holds it. Whatever it
                came from is linked below the answer.
              </p>
            </div>
            <div className="feat-art">
              <AnswerArt />
            </div>
          </div>

          <div className="feat feat-flip" data-reveal>
            <div className="feat-copy">
              <h3>Charts and insights</h3>
              <p>
                Ask, &ldquo;Show commits by author this quarter,&rdquo; and Handbook turns
                activity from your connected tools into a clear chart.
              </p>
            </div>
            <div className="feat-art">
              <ChartArt />
            </div>
          </div>

          <div className="feat" data-reveal>
            <div className="feat-copy">
              <h3>Scheduled reports</h3>
              <p>
                Choose what you want to follow and set a daily, weekly or monthly
                schedule. Handbook emails you only what changed.
              </p>
            </div>
            <div className="feat-art">
              <InboxArt />
            </div>
          </div>
        </div>
      </section>

      <section className="show-band" aria-labelledby="setup-title">
        <div className="landing-wrap">
          <div className="show-head" data-reveal>
            <p className="landing-eyebrow">Getting started</p>
            <h2 id="setup-title" className="show-title">
              Set up what your team needs.
            </h2>
            <p className="show-lead">
              An admin connects the tools, invites the team and sets any optional
              controls. Everything can be changed later.
            </p>
          </div>

          <div className="setup">
            <ul className="show-list">
              <li className="show-item" data-reveal data-setup-step="0">
                <span className="show-item-num">01</span>
                <div>
                  <h3>Connect your tools</h3>
                  <p>
                    An admin connects Notion, Drive, Slack, Linear, GitHub or Google
                    Forms and chooses what Handbook can read.
                  </p>
                </div>
              </li>
              <li className="show-item" data-reveal data-setup-step="1">
                <span className="show-item-num">02</span>
                <div>
                  <h3>Invite your team</h3>
                  <p>
                    Invite teammates by email. They sign in with a secure link, so
                    there is no extra password to manage.
                  </p>
                </div>
              </li>
              <li className="show-item" data-reveal data-setup-step="2">
                <span className="show-item-num">03</span>
                <div>
                  <h3>Add spaces, if you need them</h3>
                  <p>
                    Create a separate space for a project or department. Its members
                    and connected tools stay separate from the rest of the company.
                  </p>
                </div>
              </li>
              <li className="show-item" data-reveal data-setup-step="3">
                <span className="show-item-num">04</span>
                <div>
                  <h3>Choose an AI model, or use ours</h3>
                  <p>
                    Use the built-in model or connect a provider your company already
                    uses. Handbook tests the connection before saving it.
                  </p>
                </div>
              </li>
            </ul>

            <HowSetupTracker
              steps={[
                "Connect your tools",
                "Invite your team",
                "Add spaces",
                "Choose a model",
              ]}
            />
          </div>
        </div>
      </section>

      <section className="show-band show-band-tint vow landing-wrap" aria-labelledby="privacy-title">
        <span className="vow-aura" aria-hidden />
        <p className="landing-eyebrow" data-reveal>
          Your data
        </p>
        <h2 id="privacy-title" className="vow-title" data-reveal style={{ ["--i" as string]: "1" }}>
          Your data stays private,
          <span className="show-title-accent"> always.</span>
        </h2>
        <p className="vow-lead" data-reveal style={{ ["--i" as string]: "2" }}>
          Handbook uses your connected information only to answer your team&rsquo;s
          questions. It is not used to train AI models or shared with other
          companies.
        </p>
        <ul className="vow-points">
          <li data-reveal style={{ ["--i" as string]: "3" }}>
            <strong>Not used for training</strong>
            Your documents are not used to improve AI models.
          </li>
          <li data-reveal style={{ ["--i" as string]: "4" }}>
            <strong>Only your team can access it</strong>
            Your company&rsquo;s information stays separate from other companies and
            spaces.
          </li>
          <li data-reveal style={{ ["--i" as string]: "5" }}>
            <strong>Survey responses stay anonymous</strong>
            Handbook shows group-level sentiment without naming individual people.
          </li>
        </ul>
      </section>

      <section className="how-close landing-wrap" aria-labelledby="how-close">
        <div className="how-close-panel has-spotlight" data-reveal>
          <p className="landing-eyebrow">Getting started</p>
          <h2 id="how-close">Connect a tool and try it yourself.</h2>
          <p className="how-close-lead">
            Connect one tool, ask a question you already know, and see how Handbook
            finds the answer.
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
