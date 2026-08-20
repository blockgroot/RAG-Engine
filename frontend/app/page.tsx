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
            Connect Notion, Google Drive, Slack, Linear, and GitHub. Handbook answers
            employee questions from your connected content — and says when the
            documents don&rsquo;t cover it.
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

      <section id="what" className="landing-section landing-wrap" aria-labelledby="what-title">
        <div className="landing-section-head">
          <div>
            <p className="landing-eyebrow">Built for work</p>
            <h2 id="what-title" className="landing-section-title">
              Documents, code, and team spaces — grounded.
            </h2>
          </div>
          <p className="landing-section-lead">
            One place to ask. Answers come from the content your company
            connected, scoped to the right tenant every time.
          </p>
          <p className="landing-privacy-note">
            We index your content to answer questions — never to train AI
            models, never shared with another company.
          </p>
        </div>
        <div className="landing-feature-grid">
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="document" size={22} />
            </span>
            <h3>Grounded document answers</h3>
            <p>
              Leave, benefits, expenses, handbooks, or any other doc — retrieved
              from your synced content, grounded in what your company connected.
            </p>
          </article>
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="github" size={22} />
            </span>
            <h3>Live code context</h3>
            <p>
              Ask about READMEs and recent commits through GitHub at question
              time — always against the repositories you authorized.
            </p>
          </article>
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="workspace" size={22} />
            </span>
            <h3>Team workspaces</h3>
            <p>
              Create a focused space for a project or team, invite colleagues,
              and keep questions on that space&rsquo;s own connected content.
            </p>
          </article>
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="secure" size={22} />
            </span>
            <h3>Tenant isolation</h3>
            <p>
              Every search is scoped to your organization. Admins invite
              members by email, and other companies never see your data.
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
            conversations, and Linear for issues. Use GitHub live for code.
            Sign in with a magic link — no extra password to manage.
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
              invited — then every question is answered from that content.
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
