"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LandingShell } from "@/components/LandingShell";
import { AnswerFigure, AskSteps } from "@/components/site/AnswerFigure";
import { HeroFigure } from "@/components/site/HeroFigure";
import { HeroFacts } from "@/components/site/HeroFacts";
import { ModesFigure } from "@/components/site/ModesFigure";
import { AccessFigure } from "@/components/site/AccessFigure";
import { CloseFigure } from "@/components/site/CloseFigure";
import { Receipts, Scoreboard } from "@/components/site/Proof";
import { TrustList } from "@/components/site/TrustList";
import { SetupSteps } from "@/components/site/SetupSteps";
import { api } from "@/lib/api";
import { homePathFor } from "@/lib/routing";

/**
 * Public product story. Signed-in users skip it; everyone else should leave
 * knowing what Handbook DOES for them, in their words: ask your own tools a
 * question and get a checkable answer, a chart, or a summary in your inbox.
 *
 * Copy rule (here and /how-it-works): the outcome, never the machinery. Say
 * "your documents" and "your tools", not "retrieval" or "the corpus".
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
            tools and replies with the answer and where it came from, in the
            web app or right in Slack.
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
            <Link href="/security" className="sec-more">How Handbook keeps data private &rarr;</Link>
          </div>
        </div>
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

      <section id="deploy" className="site-wrap sec" aria-labelledby="deploy-title">
        <div className="deploy-teaser">
          <div>
            <p className="site-label">Deployment</p>
            <h2 id="deploy-title" className="sec-title">Our cloud, or your own servers.</h2>
            <p className="sec-lead">
              Start on Cloud with nothing to install, or run the same Handbook inside your company.
            </p>
          </div>
          <Link href="/deployment" className="site-btn site-btn-ghost">Compare the two &rarr;</Link>
        </div>
      </section>

      <section className="site-wrap sec" id="start" aria-labelledby="start-title">
        <div className="sec-head">
          <p className="site-label">Get started</p>
          <h2 id="start-title" className="sec-title">Set up what your team needs.</h2>
          <p className="sec-lead">
            An admin connects the tools and invites the team. Everything else is optional and can be
            changed later.
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
