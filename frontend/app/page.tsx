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
            Connect the tools your company already works in. Your team asks
            questions in ordinary language and gets answers from what is inside
            them, with a link to the source every time.
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
            We never train on your data and never share it. Your documents are used
            to answer your own team&rsquo;s questions, and nothing else.
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
            One place to ask,
            <span className="show-title-accent"> whatever it is about.</span>
          </h2>
          <p className="show-lead">
            One box covers policies, project decisions, tickets and code. You do not
            choose a tool or a category first &mdash; Handbook works out where the
            answer lives, and shows you the document it used.
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
            If your connected tools do not contain the answer, Handbook says so
            rather than guessing.
          </p>
        </div>

        <ul className="show-list">
          <li className="show-item" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-item-mark">
              <FeatureIcon name="document" />
            </span>
            <div>
              <h3>Answers from your documents</h3>
              <p>
                Policies, expenses, onboarding, meeting notes and Slack decisions.
                Each answer names the document behind it and who last edited it, so
                you can open the source or check that it is still current.
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
                What a repository is for, what changed recently, which pull requests
                are open and who reviewed them. GitHub is read at the moment you ask,
                so the answer reflects the repository as it stands now.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-item-mark">
              <FeatureIcon name="chart" />
            </span>
            <div>
              <h3>Charts for anything countable</h3>
              <p>
                Ask for a figure over time and Handbook draws it, labelled and
                colour-coded. Hovering a bar or slice lists the individual items it
                counted.
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
              Kept current without anyone maintaining it.
            </h2>
            <p className="show-lead">
              Once your tools are connected there is nothing to run and nothing to
              remember. Handbook refreshes what it has read, sends the updates you
              asked for, and measures the surveys you point it at.
            </p>
          </div>

          <ol className="show-flow" aria-label="What happens on its own">
            <li className="show-step" data-reveal style={{ ["--i" as string]: "0" }}>
              <span className="show-step-mark">
                <BrandGlyph name="drive" size={22} />
              </span>
              <h3>Kept up to date automatically</h3>
              <p>
                Edited pages, new messages and closed tickets are picked up through
                the day, so answers reflect recent work. Each source also shows when
                it was last checked.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "1" }}>
              <span className="show-step-mark">
                <FeatureIcon name="schedule" />
              </span>
              <h3>Reports by email</h3>
              <p>
                Describe what you want to track, choose daily, weekly or monthly, and
                Handbook emails a summary on that schedule. Each report covers only
                what changed since the last one.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "2" }}>
              <span className="show-step-mark">
                <FeatureIcon name="sentiment" />
              </span>
              <h3>Survey sentiment</h3>
              <p>
                Connect a Google Form and see which topics your team is positive or
                negative about. Responses are never stored and nobody is named; a
                topic with fewer than five replies is not shown at all.
              </p>
            </li>
          </ol>
        </div>
      </section>

      <section id="control" className="show-band landing-wrap" aria-labelledby="control-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">Under your control</p>
          <h2 id="control-title" className="show-title">
            You decide the boundaries.
          </h2>
          <p className="show-lead">
            Which tools are connected, who can see them, and which AI model answers
            are all settings an admin controls, and all of them can be changed
            later.
          </p>
        </div>

        <div className="show-tiles">
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-tile-mark">
              <FeatureIcon name="model" />
            </span>
            <h3>Use the AI you prefer</h3>
            <p>
              If your company already has an account with OpenAI, Anthropic, Google
              or another provider, add that key and your team can select the model
              when they ask. Handbook tests it before saving.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "1" }}>
            <span className="show-tile-mark">
              <FeatureIcon name="workspace" />
            </span>
            <h3>A space for each team</h3>
            <p>
              A space is a smaller area with its own connected tools and its own
              members. Questions asked there are answered only from that
              space&rsquo;s material.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-tile-mark">
              <FeatureIcon name="secure" />
            </span>
            <h3>Private by default</h3>
            <p>
              Only people an admin invited can sign in, and only your company can
              search your material. Disconnect a tool and the copy Handbook kept of
              it is deleted.
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
            <h2 id="close-title">Up and running in three steps.</h2>
            <p className="landing-close-lead">
              Connect one tool, invite your team, and ask your first question.
              Setup takes a few minutes per tool, and you can add the rest whenever
              you are ready.
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
