"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BrandGlyph } from "@/components/BrandGlyph";
import { LandingProductArt } from "@/components/LandingProductArt";
import { LandingPromptCycle } from "@/components/LandingPromptCycle";
import { RevealOnScroll, useSpotlight } from "@/components/Reveal";
import { LandingShell } from "@/components/LandingShell";
import { LandingSourcesOrbit } from "@/components/LandingSourcesOrbit";
import { api } from "@/lib/api";
import { homePathFor } from "@/lib/routing";

/**
 * Public product story. Signed-in users skip it; everyone else should leave
 * knowing *what Handbook is* (grounded Q&A over your sources), not just a name.
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
            One question box for everything your company knows
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
            Handbook connects to the tools your team already uses &mdash; Notion,
            Drive, Slack, Linear and GitHub &mdash; and answers questions from
            what&rsquo;s inside them. Ask for a chart when you need one, or have the
            update sent to you instead.
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
            Everything your team knows,
            <span className="show-title-accent"> one question away.</span>
          </h2>
          <p className="show-lead">
            No folders to search and no one to interrupt. Ask the way you would ask
            a colleague, and the answer arrives with a link to where it came from.
          </p>
        </div>

        <div className="show-demo" data-reveal>
          <div className="show-demo-glow" aria-hidden />
          <LandingPromptCycle />
          <p className="show-demo-foot">
            And when the answer isn&rsquo;t in your content, it says so instead of
            guessing.
          </p>
        </div>

        <ul className="show-list">
          <li className="show-item" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-item-mark">
              <BrandGlyph name="document" size={24} />
            </span>
            <div>
              <h3>Your documents, answered</h3>
              <p>
                Leave, expenses, onboarding, last week&rsquo;s notes. You get the
                answer and the page it came from, so you can check it in a click.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "1" }}>
            <span className="show-item-mark">
              <BrandGlyph name="github" size={24} />
            </span>
            <div>
              <h3>Your code, in plain language</h3>
              <p>
                What a repository does, what changed this week, which pull requests
                are still open and who reviewed them.
              </p>
            </div>
          </li>
          <li className="show-item" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-item-mark">
              <BrandGlyph name="chart" size={24} />
            </span>
            <div>
              <h3>A chart when a number is the answer</h3>
              <p>
                Ask for one and you get one &mdash; colour-coded, labelled, and
                hover anywhere to see what it is counting.
              </p>
            </div>
          </li>
        </ul>
      </section>

      <section id="works-for-you" className="show-band show-band-tint" aria-labelledby="auto-title">
        <div className="landing-wrap">
          <div className="show-head" data-reveal>
            <p className="landing-eyebrow">Works while you don&rsquo;t</p>
            <h2 id="auto-title" className="show-title">
              The updates find you.
            </h2>
            <p className="show-lead">
              Set it up once. Handbook keeps your sources current and sends what you
              asked to keep an eye on.
            </p>
          </div>

          <ol className="show-flow" aria-label="What happens on its own">
            <li className="show-step" data-reveal style={{ ["--i" as string]: "0" }}>
              <span className="show-step-mark">
                <BrandGlyph name="drive" size={22} />
              </span>
              <h3>Your sources stay current</h3>
              <p>
                New pages, messages and issues are picked up through the day.
                Nothing to press, and every answer tells you how fresh it is.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "1" }}>
              <span className="show-step-mark">
                <BrandGlyph name="schedule" size={22} />
              </span>
              <h3>Reports arrive in your inbox</h3>
              <p>
                Say what to watch in your own words, choose daily, weekly or
                monthly, and read the summary with links to everything behind it.
              </p>
            </li>
            <li className="show-step" data-reveal style={{ ["--i" as string]: "2" }}>
              <span className="show-step-mark">
                <BrandGlyph name="sentiment" size={22} />
              </span>
              <h3>You can see how people feel</h3>
              <p>
                Connect a survey and watch the themes, not the individuals. Nobody
                is named and no one&rsquo;s words are kept.
              </p>
            </li>
          </ol>
        </div>
      </section>

      <section id="control" className="show-band landing-wrap" aria-labelledby="control-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">On your terms</p>
          <h2 id="control-title" className="show-title">
            Your company&rsquo;s knowledge stays your company&rsquo;s.
          </h2>
          <p className="show-lead">
            Who sees what is something you decide &mdash; right down to which AI
            model answers.
          </p>
        </div>

        <div className="show-tiles">
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "0" }}>
            <span className="show-tile-mark">
              <BrandGlyph name="model" size={26} />
            </span>
            <h3>Choose your AI model</h3>
            <p>
              Prefer OpenAI, Anthropic, Google or another provider? Add your key and
              your team can pick it per question. We check it works before saving.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "1" }}>
            <span className="show-tile-mark">
              <BrandGlyph name="workspace" size={26} />
            </span>
            <h3>A space per team</h3>
            <p>
              Give a project its own space with its own sources and its own people.
              What is in one space stays in it.
            </p>
          </article>
          <article className="show-tile" data-reveal style={{ ["--i" as string]: "2" }}>
            <span className="show-tile-mark">
              <BrandGlyph name="secure" size={26} />
            </span>
            <h3>Private by default</h3>
            <p>
              Your content answers your questions and nothing else. It is never
              used to train AI models and never shared with another company.
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
            issues, GitHub for code, and Google Forms for surveys. Signing in is a
            link in your email &mdash; there&rsquo;s no extra password to look
            after.
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
                  <BrandGlyph name="secure" size={18} />
                </span>
                <span className="landing-close-step-body">
                  <strong>Ask</strong>
                  <span>Answers from your own content</span>
                </span>
              </li>
            </ol>
          </div>

          <div className="landing-close-copy">
            <p className="landing-eyebrow">Start here</p>
            <h2 id="close-title">Up and running in three steps.</h2>
            <p className="landing-close-lead">
              Connect a tool, invite your team, and start asking. From then on the
              answers come from your own content &mdash; and the updates you care
              about arrive without being asked for.
            </p>
            <div className="landing-close-actions">
              <Link href="/signup" className="button landing-cta-primary landing-close-primary">
                Request access
              </Link>
              <Link href="/how-it-works" className="landing-close-link">
                See how a question is answered
              </Link>
            </div>
          </div>
        </div>
      </section>
    </LandingShell>
  );
}
