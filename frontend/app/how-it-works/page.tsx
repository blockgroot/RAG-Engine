import type { Metadata } from "next";
import Link from "next/link";
import { BrandGlyph } from "@/components/BrandGlyph";
import { LandingShell } from "@/components/LandingShell";

export const metadata: Metadata = {
  title: "How it works",
  description:
    "How Handbook turns a question into a cited answer — retrieve, gate, generate — and when it refuses.",
};

export default function HowItWorksPage() {
  return (
    <LandingShell active="how">
      <section className="how-hero landing-wrap" aria-labelledby="how-title">
        <p className="landing-eyebrow">The path of a question</p>
        <h1 id="how-title" className="how-title">
          How Handbook answers — and when it refuses.
        </h1>
        <p className="how-lead">
          There is no mystery box. A question is retrieved against your tenant,
          scored, then answered only from those chunks. If the documents
          don&rsquo;t cover it, we say we don&rsquo;t know.
        </p>
      </section>

      <div className="how-flow landing-wrap" aria-label="Answer pipeline">
        <article className="how-flow-step">
          <span className="how-flow-num">1</span>
          <strong>Ask</strong>
          <p>A follow-up is rewritten into a standalone question first.</p>
        </article>
        <span className="how-flow-arrow" aria-hidden />
        <article className="how-flow-step">
          <span className="how-flow-num">2</span>
          <strong>Retrieve</strong>
          <p>Hybrid search over your org&rsquo;s chunks — never another company&rsquo;s.</p>
        </article>
        <span className="how-flow-arrow" aria-hidden />
        <article className="how-flow-step">
          <span className="how-flow-num">3</span>
          <strong>Gate</strong>
          <p>If the top match is too weak, we skip the model and refuse.</p>
        </article>
        <span className="how-flow-arrow" aria-hidden />
        <article className="how-flow-step">
          <span className="how-flow-num">4</span>
          <strong>Cite</strong>
          <p>The model may only use the retrieved text. Sources stay visible.</p>
        </article>
      </div>

      <section className="landing-section landing-wrap" aria-labelledby="paths-title">
        <div className="landing-section-head">
          <div>
            <p className="landing-eyebrow">Two agents, one desk</p>
            <h2 id="paths-title" className="landing-section-title">
              Policies are indexed. Code is live.
            </h2>
          </div>
          <p className="landing-section-lead">
            You pick Policies or Code in the chat header. Nothing is auto-routed
            by a classifier — the tab is the decision.
          </p>
        </div>

        <div className="how-paths">
          <article className="how-path">
            <header className="how-path-head">
              <BrandGlyph name="notion" size={28} />
              <BrandGlyph name="drive" size={28} />
              <div>
                <strong>Policies</strong>
                <span>Notion · Google Drive</span>
              </div>
            </header>
            <ol>
              <li>An admin connects a source and syncs.</li>
              <li>Pages are chunked, embedded, and stored with your org id.</li>
              <li>A question retrieves those chunks, then must clear the gate.</li>
              <li>The answer is generated from that context only, with citations.</li>
            </ol>
          </article>

          <article className="how-path">
            <header className="how-path-head">
              <BrandGlyph name="github" size={28} />
              <div>
                <strong>Code</strong>
                <span>GitHub, at question time</span>
              </div>
            </header>
            <ol>
              <li>Nothing is embedded — no README index, no stale dump.</li>
              <li>The model may call one bounded GitHub tool (README, commits).</li>
              <li>The repo name is checked against the install you authorized.</li>
              <li>No tool call, or a miss, returns the same honest fallback.</li>
            </ol>
          </article>
        </div>
      </section>

      <section className="landing-section landing-wrap" aria-labelledby="isolate-title">
        <div className="landing-section-head">
          <div>
            <p className="landing-eyebrow">Who can see what</p>
            <h2 id="isolate-title" className="landing-section-title">
              Company first. Space second.
            </h2>
          </div>
          <p className="landing-section-lead">
            Isolation is a filter on every read, not a later check. A workspace
            never silently inherits the company wiki.
          </p>
        </div>
        <div className="how-isolate">
          <article>
            <BrandGlyph name="secure" size={26} />
            <h3>Tenant isolation</h3>
            <p>
              Every read is filtered by org — and by workspace when you&rsquo;re
              in one. Company A cannot retrieve Company B.
            </p>
          </article>
          <article>
            <BrandGlyph name="workspace" size={26} />
            <h3>Workspaces stay private</h3>
            <p>
              A space for meeting notes answers only from its own connected
              source. Org-wide HR chunks never leak into it.
            </p>
          </article>
          <article>
            <BrandGlyph name="gmail" size={26} />
            <h3>Magic-link, invited members</h3>
            <p>
              Sign-in is email. An admin invites people by address. There is no
              public signup into someone else&rsquo;s company.
            </p>
          </article>
        </div>
      </section>

      <section className="landing-close landing-wrap" aria-labelledby="how-close">
        <h2 id="how-close">That&rsquo;s the whole product.</h2>
        <p>
          Connect a source, invite the team, ask a real question. If the page
          exists, you get the line. If it doesn&rsquo;t, you get an honest no.
        </p>
        <div className="landing-cta-row">
          <Link href="/signup" className="button landing-cta-primary">
            Request access
          </Link>
          <Link href="/" className="button button-secondary landing-cta-ghost">
            Back to product
          </Link>
        </div>
      </section>
    </LandingShell>
  );
}
