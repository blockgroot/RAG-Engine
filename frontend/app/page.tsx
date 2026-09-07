"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BrandGlyph } from "@/components/BrandGlyph";
import { FeatureEmoji } from "@/components/FeatureEmoji";
import { LandingProductArt } from "@/components/LandingProductArt";
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
            Your team&rsquo;s knowledge, in one place
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
            Connect Notion, Drive, Slack, Linear or GitHub, and your team can just
            ask. How much leave do I have left? What did we decide about pricing?
            What shipped this week? Every answer comes with a link, so you can see
            for yourself.
          </p>
          <div className="landing-cta-row" data-reveal style={{ ["--i" as string]: "3" }}>
            <Link href="/signup" className="button landing-cta-primary">
              Get started
            </Link>
            <Link href="/how-it-works" className="button button-secondary landing-cta-ghost">
              See how it works
            </Link>
          </div>
        </div>
        <LandingProductArt />
      </section>

      {/* Composition varies on purpose: a showcase, then a plain list, then
          a connected flow, then wash tiles. The page used to be four grids of
          the same bordered card, which reads as filler however good the copy
          is. Nothing here is boxed unless the box earns it. */}

      <section id="what" className="show-band landing-wrap" aria-labelledby="what-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">One place to ask</p>
          <h2 id="what-title" className="show-title">
            Ask like you&rsquo;d ask
            <span className="show-title-accent"> a colleague.</span>
          </h2>
          <p className="show-lead">
            No digging through folders. No waiting for someone to reply. Type the
            question you were about to ask in Slack, and get the answer instead.
          </p>
        </div>

        <div className="show-demo" data-reveal>
          <div className="show-demo-glow" aria-hidden />
          <LandingPromptCycle />
          <p className="show-demo-foot">
            And if the answer really isn&rsquo;t in your tools, it tells you that
            too.
          </p>
        </div>

        <ul className="show-list">
          <li className="show-item" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-item-mark">
              <FeatureEmoji name="document" />
            </span>
            <div>
              <h3>Answers from your documents</h3>
              <p>
                Leave, expenses, onboarding, last week&rsquo;s meeting notes. You get
                a straight answer, plus the page it came from if you want to read the
                rest.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "1" }}>
            <span className="show-item-mark">
              <BrandGlyph name="github" size={24} />
            </span>
            <div>
              <h3>Answers about your code</h3>
              <p>
                What a codebase is for, what changed this week, which pull requests
                are waiting and who has looked at them. Useful whether or not you
                write the code yourself.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-item-mark">
              <FeatureEmoji name="chart" />
            </span>
            <div>
              <h3>Charts, when a number says it better</h3>
              <p>
                Ask for a chart and you get a proper one, clearly labelled. Point at
                any part of it to see what went into that piece.
              </p>
            </div>
          </li>
        </ul>
      </section>

      <section id="works-for-you" className="show-band show-band-tint" aria-labelledby="auto-title">
        <div className="landing-wrap">
          <div className="show-head" data-reveal>
            <p className="landing-eyebrow">While you get on with work</p>
            <h2 id="auto-title" className="show-title">
              The updates come to you.
            </h2>
            <p className="show-lead">
              Set it up once and forget about it. Handbook keeps everything current,
              and the updates you asked for turn up on their own.
            </p>
          </div>

          <ol className="show-flow" aria-label="What happens on its own">
            <li className="show-step" data-reveal style={{ ["--i" as string]: "0" }}>
              <span className="show-step-mark">
                <BrandGlyph name="drive" size={22} />
              </span>
              <h3>Nothing to keep up to date</h3>
              <p>
                Edit a doc or close a ticket and Handbook notices. There&rsquo;s no
                sync button to remember, and answers tell you when they were last
                checked.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "1" }}>
              <span className="show-step-mark">
                <FeatureEmoji name="schedule" />
              </span>
              <h3>A summary in your inbox</h3>
              <p>
                Tell it what you want to stay on top of, pick daily, weekly or
                monthly, and read it with your coffee. Every point links to the real
                thing.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "2" }}>
              <span className="show-step-mark">
                <FeatureEmoji name="sentiment" />
              </span>
              <h3>A read on how the team feels</h3>
              <p>
                Link a survey and see which topics people are happy or unhappy about.
                You see the pattern, never the person &mdash; nobody is named, and no
                answers are kept.
              </p>
            </li>
          </ol>
        </div>
      </section>

      <section id="control" className="show-band landing-wrap" aria-labelledby="control-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">Your call</p>
          <h2 id="control-title" className="show-title">
            What&rsquo;s yours stays yours.
          </h2>
          <p className="show-lead">
            You decide who sees what, which teams get their own space, and even
            which AI does the answering.
          </p>
        </div>

        <div className="show-tiles">
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-tile-mark">
              <FeatureEmoji name="model" />
            </span>
            <h3>Use the AI you prefer</h3>
            <p>
              Already paying for OpenAI, Anthropic or Google? Add your key and your
              team can choose it whenever they ask something. We test it first, so
              nobody lands on a broken option.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "1" }}>
            <span className="show-tile-mark">
              <FeatureEmoji name="workspace" />
            </span>
            <h3>A space for each team</h3>
            <p>
              Give a project or a department its own space, with its own tools and
              its own people. What belongs to one team stays there.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-tile-mark">
              <FeatureEmoji name="secure" />
            </span>
            <h3>Private, by default</h3>
            <p>
              Your documents are read to answer your team&rsquo;s questions and
              nothing else. Never used to train AI, never shown to anyone outside
              your company.
            </p>
          </article>
        </div>
      </section>

      <section id="sources" className="landing-section landing-section-orbit landing-wrap" aria-labelledby="sources-title">
        <div className="landing-section-head" data-reveal>
          <div>
            <p className="landing-eyebrow">Works with your tools</p>
            <h2 id="sources-title" className="landing-section-title">
              Connect once. Ask anywhere.
            </h2>
          </div>
          <p className="landing-section-lead">
            Notion and Drive for documents, Slack for conversations, Linear for
            tickets, GitHub for code, Google Forms for surveys. Everyone signs in
            with a link we email them, so there&rsquo;s no new password for anyone
            to forget.
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
                  <FeatureEmoji name="secure" />
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
            <h2 id="close-title">Up and running in three steps.</h2>
            <p className="landing-close-lead">
              Connect one tool, invite a few people, ask your first question. It
              takes about an afternoon, and you&rsquo;ll know quickly whether it
              earns a place in your week.
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
