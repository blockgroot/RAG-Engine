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
          <p className="landing-eyebrow">Work AI that stays on your documents</p>
          <h1 id="landing-title" className="landing-title">
            Ask your company.
            <br />
            Get the real answer.
          </h1>
          <p className="landing-subtitle">
            Employees ask about leave, expenses, and the codebase. Handbook
            answers only from the Notion, Drive, or GitHub you connected — with
            citations. If it isn&rsquo;t written down, we say so.
          </p>
          <div className="landing-cta-row">
            <Link href="/signup" className="button landing-cta-primary">
              Set up your company
            </Link>
            <Link href="/how-it-works" className="button button-secondary landing-cta-ghost">
              See how it works
            </Link>
          </div>
        </div>
        <LandingProductArt />
      </section>

      <section id="what" className="landing-section landing-wrap" aria-labelledby="what-title">
        <div className="landing-section-head">
          <div>
            <p className="landing-eyebrow">What it actually does</p>
            <h2 id="what-title" className="landing-section-title">
              One desk for policies and code.
            </h2>
          </div>
          <p className="landing-section-lead">
            Connect the sources you already use. People ask in plain language.
            Handbook retrieves from <em>that</em> tenant only, then answers with
            citations — or refuses when the documents don&rsquo;t cover it.
          </p>
        </div>
        <div className="landing-feature-grid">
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="notion" size={22} />
            </span>
            <h3>Policies, grounded</h3>
            <p>
              Leave, benefits, expenses — retrieved from the pages you ingested.
              Every answer shows the source. Guessing is a product bug here, not
              a feature.
            </p>
          </article>
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="github" size={22} />
            </span>
            <h3>Code, live</h3>
            <p>
              The Code tab talks to GitHub at question time — READMEs and
              commits, no stale index. Unauthorized repos never leave the
              allowlist.
            </p>
          </article>
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="workspace" size={22} />
            </span>
            <h3>Spaces inside the company</h3>
            <p>
              A workspace is a private desk for meeting notes or a project wiki.
              Questions asked there never blend in org-wide HR docs.
            </p>
          </article>
          <article className="landing-feature">
            <span className="landing-feature-mark">
              <BrandGlyph name="secure" size={22} />
            </span>
            <h3>Your tenant, only</h3>
            <p>
              Isolation is a query filter, not a hope. Company A cannot retrieve
              Company B. Magic-link login; admins invite the rest of the team.
            </p>
          </article>
        </div>
      </section>

      <section id="sources" className="landing-section landing-section-orbit landing-wrap" aria-labelledby="sources-title">
        <div className="landing-section-head">
          <div>
            <p className="landing-eyebrow">Connects to what you already have</p>
            <h2 id="sources-title" className="landing-section-title">
              Plug in the tools. Ask once.
            </h2>
          </div>
          <p className="landing-section-lead">
            An admin connects Notion or Drive and syncs. GitHub is live —
            nothing to embed. Sign-in is email, not another password.
          </p>
        </div>
        <LandingSourcesOrbit />
      </section>

      <section className="landing-close landing-wrap" aria-labelledby="close-title">
        <h2 id="close-title">Ready when your documents are.</h2>
        <p>
          Request access, get approved, connect a source, and ask the question
          your wiki already answered — without hunting for the page.
        </p>
        <div className="landing-cta-row">
          <Link href="/signup" className="button landing-cta-primary">
            Request access
          </Link>
          <Link href="/how-it-works" className="button button-secondary landing-cta-ghost">
            How a question is answered
          </Link>
        </div>
      </section>
    </LandingShell>
  );
}
