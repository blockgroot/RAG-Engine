"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LandingShell } from "@/components/LandingShell";
import { AnswerFigure, AskSteps } from "@/components/site/AnswerFigure";
import { HeroFigure } from "@/components/site/HeroFigure";
import { HeroFacts } from "@/components/site/HeroFacts";
import { ModesFigure } from "@/components/site/ModesFigure";
import { Surfaces } from "@/components/site/Surfaces";
import { AccessFigure } from "@/components/site/AccessFigure";
import { Background } from "@/components/site/Background";
import { CloseFigure } from "@/components/site/CloseFigure";
import { ControlPanel } from "@/components/site/ControlPanel";
import { Receipts, Scoreboard } from "@/components/site/Proof";
import { SetupSteps } from "@/components/site/SetupSteps";
import { TrustList } from "@/components/site/TrustList";
import { OwnAppsFigure } from "@/components/site/OwnAppsFigure";
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
    <LandingShell>
      <section className="site-wrap hero" aria-labelledby="landing-title">
        <div>
          <p className="site-label">Your company&rsquo;s knowledge, answered</p>
          <h1 id="landing-title" className="hero-title">
            Ask your workplace <em>anything.</em>
          </h1>
          <p className="hero-lead">
            Handbook connects to Notion, Drive, Slack, Linear and GitHub. Ask a
            question and get an answer with its source, dig into a broad
            question, chart the numbers, or draft an update you approve before
            it&rsquo;s sent.
          </p>
          <div className="hero-ctas">
            <Link href="/signup" className="site-btn site-btn-solid">
              Get started
            </Link>
            <Link href="#how" className="site-btn site-btn-ghost">
              See how it works &rarr;
            </Link>
          </div>
        </div>
        <HeroFigure />
      </section>

      <HeroFacts />

      <section id="how" className="site-wrap sec" aria-labelledby="how-title">
        <div className="sec-head">
          <p className="site-label">How it works</p>
          <h2 id="how-title" className="sec-title">One answer, not a list of links.</h2>
          <p className="sec-lead">
            Ask the way you would ask a colleague. Handbook reads across your
            tools and replies with the answer and where it came from, so you
            never have to open five tabs to find it.
          </p>
        </div>
        <AnswerFigure />
        <AskSteps />
      </section>

      <section id="modes" className="site-wrap sec" aria-labelledby="modes-title">
        <div className="sec-head">
          <p className="site-label">Four modes, one chat</p>
          <h2 id="modes-title" className="sec-title">Answer, investigate, chart or act.</h2>
          <p className="sec-lead">
            Switch modes right above the message box. Each one returns a
            different kind of result, and every result shows where it came from.
          </p>
        </div>
        <ModesFigure />
      </section>

      <section id="surfaces" className="site-wrap sec" aria-labelledby="surfaces-title">
        <div className="sec-head">
          <p className="site-label">Where you use it</p>
          <h2 id="surfaces-title" className="sec-title">In the places your team already works.</h2>
        </div>
        <Surfaces />
      </section>

      <section id="security" className="site-wrap sec" aria-labelledby="security-title">
        <div className="sec-split">
          <AccessFigure />
          <div>
            <p className="site-label">Access and privacy</p>
            <h2 id="security-title" className="sec-title">Each person sees only what they can open.</h2>
            <p className="sec-lead">
              Handbook copies each tool&rsquo;s sharing along with the content and
              checks it for every person, before anything is searched. Two
              colleagues can ask the same question and get answers from
              different documents.
            </p>
            <TrustList />
          </div>
        </div>
      </section>

      <section id="control" className="site-wrap sec" aria-labelledby="control-title">
        <div className="sec-head">
          <p className="site-label">Under your control</p>
          <h2 id="control-title" className="sec-title">Admins set the boundaries.</h2>
          <p className="sec-lead">
            Choose the model, separate teams into spaces, and decide what Agent
            mode may do. Everything can be changed later.
          </p>
        </div>
        <ControlPanel />
      </section>

      <section id="own-apps" className="site-wrap sec" aria-labelledby="own-apps-title">
        <div className="oa">
          <div className="sec-head">
            <p className="site-label">Google and Slack</p>
            <h2 id="own-apps-title" className="sec-title">Connected through your own app.</h2>
            <p className="sec-lead">
              Your company creates its own Google and Slack app once, in about 10 and 5 minutes.
              Handbook walks you through each step, with every value ready to copy.
            </p>
            <ul className="oa-why">
              <li>Everyone in your company can sign in, with no Google review</li>
              <li>Slack is read at full speed</li>
              <li>Your data travels only between your company and Handbook</li>
            </ul>
          </div>
          <OwnAppsFigure />
        </div>
      </section>

      <section id="background" className="site-wrap sec" aria-labelledby="background-title">
        <div className="sec-head">
          <p className="site-label">In the background</p>
          <h2 id="background-title" className="sec-title">Keeps working after you ask.</h2>
          <p className="sec-lead">
            Handbook stays up to date on its own, and notices what people
            could not find.
          </p>
        </div>
        <Background />
      </section>

      <section id="results" className="site-wrap sec" aria-labelledby="results-title">
        <div className="sec-split">
          <div>
            <p className="site-label">Head to head</p>
            <h2 id="results-title" className="sec-title">Answers you can trust. No guessing.</h2>
            <p className="sec-lead">
              We gave Handbook and another leading assistant the same test:
              the same questions, the same documents, the same AI model.
              Every answer was graded blind.
            </p>
          </div>
          <Scoreboard />
        </div>
        <div className="sec-split is-flip" style={{ marginTop: "clamp(3rem, 7vw, 5rem)" }}>
          <div>
            <p className="site-label">Speed and cost</p>
            <h2 className="sec-title">Faster answers, a fraction of the text.</h2>
            <p className="sec-lead">
              Handbook reads what the question needs instead of everything
              nearby: about one-sixteenth of the text per question, so answers
              start sooner and cost less.
            </p>
          </div>
          <Receipts />
        </div>
      </section>

      <section id="start" className="site-wrap sec" aria-labelledby="start-title">
        <div className="sec-head">
          <p className="site-label">Get started</p>
          <h2 id="start-title" className="sec-title">Set up what your team needs.</h2>
          <p className="sec-lead">
            An admin connects the tools and invites the team. Everything else is
            optional and can be changed later.
          </p>
        </div>
        <SetupSteps />
      </section>

      <section className="site-wrap sec close" aria-labelledby="close-title">
        <div className="sec-split">
          <div>
            <p className="site-label">Start here</p>
            <h2 id="close-title" className="sec-title">Connect one tool and ask a question you already know.</h2>
            <p className="sec-lead">
              If the answer matches what you expected, and links where it came
              from, you will know how far to trust the rest.
            </p>
            <div className="hero-ctas">
              <Link href="/signup" className="site-btn site-btn-solid">Request access</Link>
              <Link href="/login" className="site-btn site-btn-ghost">Sign in</Link>
            </div>
          </div>
          <CloseFigure />
        </div>
      </section>
    </LandingShell>
  );
}
