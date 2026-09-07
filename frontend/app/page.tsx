"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BrandGlyph } from "@/components/BrandGlyph";
import { FeatureIcon } from "@/components/FeatureIcon";
import { LandingProductArt } from "@/components/LandingProductArt";
import { AnswerArt } from "@/components/HowAnswerArt";
import { LandingPromptCycle } from "@/components/LandingPromptCycle";
import { RevealOnScroll, useSpotlight } from "@/components/Reveal";
import { LandingShell } from "@/components/LandingShell";
import { LandingSourcesOrbit } from "@/components/LandingSourcesOrbit";
import { api } from "@/lib/api";
import { homePathFor } from "@/lib/routing";

/**
 * Public product story. Signed-in users skip it; everyone else should leave
 * knowing what Handbook DOES for them, in their words: ask your own tools a
 * question and get a checkable answer, a chart, or a summary in your inbox.
 *
 * Copy rule for this page and /how-it-works: describe the outcome, never the
 * machinery. "Grounded", "org-scoped", "retrieval" and "the confidence gate"
 * are all real and all documented in CLAUDE.md — a visitor deciding whether to
 * try this does not need any of them, and naming them made the page read like
 * an internal design doc. Say "your documents" and "your tools", not "your
 * content" or "the corpus".
 */
export default function RootPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const heroRef = useRef<HTMLElement>(null);
  useSpotlight(heroRef);

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (!cancelled) router.replace(homePathFor(me));
      })
      .catch(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (!ready) {
    return (
      <main className="landing">
        <p className="landing-loading muted">Loading…</p>
      </main>
    );
  }

  return (
    <LandingShell active="home">
      <RevealOnScroll />
      <section
        ref={heroRef}
        className="landing-hero landing-hero-split landing-wrap has-spotlight"
        aria-labelledby="landing-title"
      >
        <div className="landing-hero-copy">
          <p className="landing-eyebrow" data-reveal style={{ ["--i" as string]: "0" }}>
            Your team&rsquo;s answers, in one place
          </p>
          <h1
            id="landing-title"
            className="landing-title"
            data-reveal
            style={{ ["--i" as string]: "1" }}
          >
            Ask your workplace
            <br />
            <span className="landing-title-accent">anything.</span>
          </h1>
          <p className="landing-subtitle" data-reveal style={{ ["--i" as string]: "2" }}>
            Connect the tools your team already uses. Ask a question in plain
            English and get a clear answer with a link to the original source.
          </p>
          <div className="landing-cta-row" data-reveal style={{ ["--i" as string]: "3" }}>
            <Link href="/signup" className="button landing-cta-primary">
              Get started
            </Link>
            <Link href="/how-it-works" className="button button-secondary landing-cta-ghost">
              See how it works
            </Link>
          </div>
          <p className="landing-privacy-note" data-reveal style={{ ["--i" as string]: "4" }}>
            Your data stays private. It is used only to answer your team&rsquo;s questions.
          </p>
        </div>
        <LandingProductArt />
      </section>

      {/* Composition varies on purpose: a showcase, then a plain list, then
          a connected flow, then wash tiles. The page used to be four grids of
          the same bordered card, which reads as filler however good the copy
          is. Nothing here is boxed unless the box earns it. */}

      <section id="what" className="show-band landing-wrap" aria-labelledby="what-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">Asking</p>
          <h2 id="what-title" className="show-title">
            Ask one question,
            <span className="show-title-accent"> across your work.</span>
          </h2>
          <p className="show-lead">
            Handbook looks across the connected tools for you. You do not need to
            remember where the answer was saved.
          </p>
        </div>

        {/* The panel used to hold one line of typing in a lot of padding, which
            read as an unfinished card. It now shows the whole exchange: the
            question being typed, the answer landing with the document it came
            from, and the other things you could have asked instead. */}
        <div className="show-demo" data-reveal>
          <div className="show-demo-glow" aria-hidden />
          <div className="show-demo-body">
            <div className="show-demo-ask">
              <LandingPromptCycle />
            </div>
            <AnswerArt bare />
          </div>
          <ul className="show-demo-chips" aria-label="Other things you could ask">
            <li>What&rsquo;s our expenses limit?</li>
            <li>What shipped last week?</li>
            <li>Who reviewed the checkout change?</li>
          </ul>
          <p className="show-demo-foot">
            If the answer is not in your connected tools, Handbook tells you clearly.
          </p>
        </div>

        <ul className="show-list">
          <li className="show-item" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-item-mark">
              <FeatureIcon name="document" />
            </span>
            <div>
              <h3>Find answers quickly</h3>
              <p>
                Ask about policies, expenses, onboarding or decisions. Every answer
                links to the document it came from.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "1" }}>
            <span className="show-item-mark">
              <BrandGlyph name="github" size={24} />
            </span>
            <div>
              <h3>Understand your codebase</h3>
              <p>
                Ask what changed, which pull requests are open, or who reviewed them.
                GitHub is checked when you ask, so the answer stays current.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-item-mark">
              <FeatureIcon name="chart" />
            </span>
            <div>
              <h3>Turn activity into charts</h3>
              <p>
                Ask for a count over time and Handbook creates a clear chart with the
                items behind each result.
              </p>
            </div>
          </li>
        </ul>
      </section>

      <section id="works-for-you" className="show-band show-band-tint" aria-labelledby="auto-title">
        <div className="landing-wrap">
          <div className="show-head" data-reveal>
            <p className="landing-eyebrow">Running in the background</p>
            <h2 id="auto-title" className="show-title">
              Keep work up to date automatically.
            </h2>
            <p className="show-lead">
              Connect your tools once. Handbook keeps information current, sends the
              reports you choose, and summarises survey feedback for you.
            </p>
          </div>

          <ol className="show-flow" aria-label="What happens on its own">
            <li className="show-step" data-reveal style={{ ["--i" as string]: "0" }}>
              <span className="show-step-mark">
                <BrandGlyph name="drive" size={22} />
              </span>
              <h3>Automatic updates</h3>
              <p>
                New and updated work is picked up throughout the day, so answers use
                recent information.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "1" }}>
              <span className="show-step-mark">
                <FeatureIcon name="schedule" />
              </span>
              <h3>Reports by email</h3>
              <p>
                Choose what to track and how often. Handbook emails a summary of what
                changed since the last report.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "2" }}>
              <span className="show-step-mark">
                <FeatureIcon name="sentiment" />
              </span>
              <h3>Survey insights</h3>
              <p>
                Connect a Google Form to see which topics your team feels positive or
                negative about. Responses stay anonymous.
              </p>
            </li>
          </ol>
        </div>
      </section>

      <section id="control" className="show-band landing-wrap" aria-labelledby="control-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">Under your control</p>
          <h2 id="control-title" className="show-title">
            Set the right boundaries.
          </h2>
          <p className="show-lead">
            Admins choose the tools, people and AI model. These settings can be
            changed whenever your team needs.
          </p>
        </div>

        <div className="show-tiles">
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-tile-mark">
              <FeatureIcon name="model" />
            </span>
            <h3>Choose your AI model</h3>
            <p>
              Use the built-in model or connect a provider your company already uses.
              Handbook checks the connection before saving it.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "1" }}>
            <span className="show-tile-mark">
              <FeatureIcon name="workspace" />
            </span>
            <h3>Separate spaces for teams</h3>
            <p>
              Give a project or department its own tools and members. Questions stay
              within that space.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-tile-mark">
              <FeatureIcon name="secure" />
            </span>
            <h3>Private by default</h3>
            <p>
              Only invited people can sign in, and only your company can search its
              information. Disconnect a tool and its stored copy is deleted.
            </p>
          </article>
        </div>
      </section>

      <section id="sources" className="landing-section landing-section-orbit landing-wrap" aria-labelledby="sources-title">
        <div className="landing-section-head" data-reveal>
          <div>
            <p className="landing-eyebrow">Works with your tools</p>
            <h2 id="sources-title" className="landing-section-title">
              Connect the tools your team already uses.
            </h2>
          </div>
          <p className="landing-section-lead">
            Notion, Drive, Slack, Linear and GitHub work together in one place. Add
            Google Forms when you want survey insights.
          </p>
        </div>
        <LandingSourcesOrbit />
      </section>

      <section className="landing-close landing-wrap" aria-labelledby="close-title">
        <div className="landing-close-panel" data-reveal>
          <div className="landing-close-orbit-wrap">
            <div className="landing-close-orbit-ring" aria-hidden>
              <span className="landing-close-orbit-glow" />
            </div>
            <div className="landing-close-orbit-core" aria-hidden>
              <span>3</span>
            </div>
            <ol className="landing-close-orbit" aria-label="Getting started path">
              <li className="landing-close-step" style={{ ["--step" as string]: "0" }}>
                <span className="landing-close-step-mark" aria-hidden>
                  <BrandGlyph name="notion" size={18} />
                </span>
                <span className="landing-close-step-body">
                  <strong>Connect</strong>
                  <span>Notion, Drive, Slack, Linear, or GitHub</span>
                </span>
              </li>
              <li className="landing-close-step" style={{ ["--step" as string]: "1" }}>
                <span className="landing-close-step-mark" aria-hidden>
                  <BrandGlyph name="sendgrid" size={20} />
                </span>
                <span className="landing-close-step-body">
                  <strong>Invite</strong>
                  <span>Your team, by email</span>
                </span>
              </li>
              <li className="landing-close-step" style={{ ["--step" as string]: "2" }}>
                <span className="landing-close-step-mark" aria-hidden>
                  <FeatureIcon name="secure" />
                </span>
                <span className="landing-close-step-body">
                  <strong>Ask</strong>
                  <span>Answers from your own tools</span>
                </span>
              </li>
            </ol>
          </div>

          <div className="landing-close-copy">
            <p className="landing-eyebrow">Start here</p>
            <h2 id="close-title">Start in three simple steps.</h2>
            <p className="landing-close-lead">
              Connect a tool, invite your team and ask your first question. Add more
              tools whenever you are ready.
            </p>
            <div className="landing-close-actions">
              <Link href="/signup" className="button landing-cta-primary landing-close-primary">
                Request access
              </Link>
              <Link href="/how-it-works" className="landing-close-link">
                See how it works
              </Link>
            </div>
          </div>
        </div>
      </section>
    </LandingShell>
  );
}
