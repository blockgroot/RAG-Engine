"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BrandGlyph } from "@/components/BrandGlyph";
import { LandingProductArt } from "@/components/LandingProductArt";
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
      <section className="landing-hero landing-hero-split landing-wrap" aria-labelledby="landing-title">
        <div className="landing-hero-copy">
          <p className="landing-eyebrow">AI for your company knowledge</p>
          <h1 id="landing-title" className="landing-title">
            Ask your workplace.
            <br />
            Answer from your sources.
          </h1>
          <p className="landing-subtitle">
            Connect Notion, Google Drive, Slack, Linear, and GitHub. Ask in plain
            language and get an answer, a chart, or a scheduled report &mdash; every
            one built from your own content, and honest when that content
            doesn&rsquo;t cover the question.
          </p>
          <div className="landing-cta-row">
            <Link href="/signup" className="button landing-cta-primary">
              Get started
            </Link>
            <Link href="/how-it-works" className="button button-secondary landing-cta-ghost">
              How it works
            </Link>
          </div>
        </div>
        <LandingProductArt />
      </section>

      {/* Three bands, not one grid of eight cards. The page grew a
          capability at a time (charts, sentiment, own model) and a single
          feature grid had stopped being a list of things you can do and
          become a wall to scan. Grouped by the QUESTION a reader is asking
          at that point: what can I ask it, what does it do without me, and
          what do I control. */}
      <section id="what" className="landing-section landing-wrap" aria-labelledby="what-title">
        <div className="landing-section-head">
          <div>
            <p className="landing-eyebrow">Ask</p>
            <h2 id="what-title" className="landing-section-title">
              One box. Words, code, or a chart.
            </h2>
          </div>
          <p className="landing-section-lead">
            There is no mode to choose. Ask a question and Handbook decides where
            the answer lives &mdash; a document, a repository, or a count it can
            draw.
          </p>
          <p className="landing-privacy-note">
            We index your content to answer questions &mdash; never to train AI
            models, never shared with another company.
          </p>
        </div>
        <div className="landing-feature-grid landing-feature-grid-3">
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="document" size={22} />
            </span>
            <h3>Grounded document answers</h3>
            <p>
              Leave, benefits, expenses, handbooks, or any other doc &mdash;
              retrieved from your synced content, with the page, the app and who
              last edited it named on every answer.
            </p>
          </article>
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="github" size={22} />
            </span>
            <h3>Live code context</h3>
            <p>
              READMEs, recent commits, open pull requests and who reviewed them,
              read at question time from the repositories you authorized.
            </p>
          </article>
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="chart" size={22} />
            </span>
            <h3>Charts, from real counts</h3>
            <p>
              &ldquo;Chart commits by author this quarter.&rdquo; Every number is
              counted in the database, never written by the model &mdash; and
              hovering a bar shows the exact rows behind it.
            </p>
          </article>
        </div>
      </section>

      <section id="works-for-you" className="landing-section landing-wrap" aria-labelledby="auto-title">
        <div className="landing-section-head">
          <div>
            <p className="landing-eyebrow">Without being asked</p>
            <h2 id="auto-title" className="landing-section-title">
              The parts that keep running on their own.
            </h2>
          </div>
          <p className="landing-section-lead">
            Nobody has to remember to press anything. Your sources stay current,
            the updates you care about arrive, and what your team tells you gets
            measured.
          </p>
        </div>
        <div className="landing-feature-grid landing-feature-grid-3">
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="schedule" size={22} />
            </span>
            <h3>Reports in your inbox</h3>
            <p>
              Say what to keep an eye on in your own words, pick daily, weekly or
              monthly, and read it in your inbox. Each report covers only what
              changed since the last one, and links to every source it used.
            </p>
          </article>
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="drive" size={22} />
            </span>
            <h3>Always up to date</h3>
            <p>
              Connected sources re-sync themselves through the day, and a
              service that can tell us it changed is picked up within minutes.
              Every answer says when its source last synced.
            </p>
          </article>
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="sentiment" size={22} />
            </span>
            <h3>Survey sentiment</h3>
            <p>
              Point Handbook at a Google Form and see how people feel by topic.
              Answers are read once and the text is discarded &mdash; no response
              and no name is ever stored, and topics with under five replies are
              never charted.
            </p>
          </article>
        </div>
      </section>

      <section id="control" className="landing-section landing-wrap" aria-labelledby="control-title">
        <div className="landing-section-head">
          <div>
            <p className="landing-eyebrow">Yours to control</p>
            <h2 id="control-title" className="landing-section-title">
              Your data, your model, your boundaries.
            </h2>
          </div>
          <p className="landing-section-lead">
            Who can see what is a decision you make, not a default you inherit
            &mdash; down to which model answers.
          </p>
        </div>
        <div className="landing-feature-grid landing-feature-grid-3">
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="model" size={22} />
            </span>
            <h3>Bring your own model</h3>
            <p>
              Add a key from OpenAI, Anthropic, Google, Mistral, Groq, DeepSeek,
              NVIDIA and more, and your team can pick it per question. We test it
              before saving, and tell you exactly which calls use it.
            </p>
          </article>
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="workspace" size={22} />
            </span>
            <h3>Team spaces</h3>
            <p>
              Create a focused space for a project or team, invite colleagues,
              and keep its questions on its own connected content &mdash; a space
              never borrows the company&rsquo;s sources.
            </p>
          </article>
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="secure" size={22} />
            </span>
            <h3>Tenant isolation</h3>
            <p>
              Every search is scoped to your organization before anything is
              ranked. Admins invite members by email, and other companies never
              see your data.
            </p>
          </article>
        </div>
      </section>

      <section id="sources" className="landing-section landing-section-orbit landing-wrap" aria-labelledby="sources-title">
        <div className="landing-section-head">
          <div>
            <p className="landing-eyebrow">Integrations</p>
            <h2 id="sources-title" className="landing-section-title">
              Connect once. Ask anywhere.
            </h2>
          </div>
          <p className="landing-section-lead">
            Sync Notion or Drive for your documents, Slack for team
            conversations, and Linear for issues. Use GitHub live for code,
            Google Forms for survey sentiment, and schedule a recurring report on
            any source that tracks activity. Sign in with a magic link &mdash; no
            extra password to manage.
          </p>
        </div>
        <LandingSourcesOrbit />
      </section>

      <section className="landing-close landing-wrap" aria-labelledby="close-title">
        <div className="landing-close-panel">
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
                  <span>Teammates by email</span>
                </span>
              </li>
              <li className="landing-close-step" style={{ ["--step" as string]: "2" }}>
                <span className="landing-close-step-mark" aria-hidden>
                  <BrandGlyph name="secure" size={18} />
                </span>
                <span className="landing-close-step-body">
                  <strong>Ask</strong>
                  <span>Grounded answers, org-scoped</span>
                </span>
              </li>
            </ol>
          </div>

          <div className="landing-close-copy">
            <p className="landing-eyebrow">Start here</p>
            <h2 id="close-title">Three steps from sources to answers.</h2>
            <p className="landing-close-lead">
              Handbook is ready once your docs are connected and your team is
              invited — then every question is answered from that content, and
              the updates you keep asking for arrive on their own.
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
