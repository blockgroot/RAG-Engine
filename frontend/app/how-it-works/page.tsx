import type { Metadata } from "next";
import Link from "next/link";
import { BrandGlyph } from "@/components/BrandGlyph";
import { LandingShell } from "@/components/LandingShell";

export const metadata: Metadata = {
  title: "How it works",
  description:
    "How Handbook turns a workplace question into a cited answer from your connected sources.",
};

export default function HowItWorksPage() {
  return (
    <LandingShell active="how">
      <section className="how-hero landing-wrap" aria-labelledby="how-title">
        <p className="landing-eyebrow">Product walkthrough</p>
        <h1 id="how-title" className="how-title">
          From question to cited answer.
        </h1>
        <p className="how-lead">
          Handbook retrieves from your company&rsquo;s connected content,
          checks confidence, then answers only from that evidence — or clearly
          says when it can&rsquo;t.
        </p>
      </section>

      <div className="how-flow landing-wrap" aria-label="Answer pipeline">
        <article className="how-flow-step">
          <span className="how-flow-num">1</span>
          <strong>Ask</strong>
          <p>Type a question. Follow-ups are rewritten into a clear standalone ask.</p>
        </article>
        <span className="how-flow-arrow" aria-hidden />
        <article className="how-flow-step">
          <span className="how-flow-num">2</span>
          <strong>Retrieve</strong>
          <p>Search your org&rsquo;s documents — never another company&rsquo;s.</p>
        </article>
        <span className="how-flow-arrow" aria-hidden />
        <article className="how-flow-step">
          <span className="how-flow-num">3</span>
          <strong>Verify</strong>
          <p>If the best match is too weak, we refuse instead of guessing.</p>
        </article>
        <span className="how-flow-arrow" aria-hidden />
        <article className="how-flow-step">
          <span className="how-flow-num">4</span>
          <strong>Answer</strong>
          <p>The reply stays on retrieved text, with sources you can open.</p>
        </article>
      </div>

      <section className="landing-section landing-wrap" aria-labelledby="paths-title">
        <div className="landing-section-head">
          <div>
            <p className="landing-eyebrow">Two modes</p>
            <h2 id="paths-title" className="landing-section-title">
              Policies from your docs. Code from GitHub.
            </h2>
          </div>
          <p className="landing-section-lead">
            Choose Policies or Code in chat. You decide the path — Handbook
            doesn&rsquo;t guess which agent to run.
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
              <li>Admin connects a source and runs a sync.</li>
              <li>Content is stored under your organization.</li>
              <li>Questions retrieve that content, then pass a confidence check.</li>
              <li>Answers include citations back to the source pages.</li>
            </ol>
          </article>

          <article className="how-path">
            <header className="how-path-head">
              <BrandGlyph name="github" size={28} />
              <div>
                <strong>Code</strong>
                <span>GitHub · live lookup</span>
              </div>
            </header>
            <ol>
              <li>Nothing is pre-indexed — reads happen when you ask.</li>
              <li>Handbook may fetch a README or recent commits once.</li>
              <li>Only repositories in your GitHub install are allowed.</li>
              <li>If nothing usable returns, you get a clear fallback.</li>
            </ol>
          </article>
        </div>
      </section>

      <section className="landing-section landing-wrap" aria-labelledby="isolate-title">
        <div className="landing-section-head">
          <div>
            <p className="landing-eyebrow">Access & safety</p>
            <h2 id="isolate-title" className="landing-section-title">
              Your company&rsquo;s boundary, end to end.
            </h2>
          </div>
          <p className="landing-section-lead">
            Isolation is built into every read. Workspaces add a second,
            tighter scope for team content inside the same company.
          </p>
        </div>
        <div className="how-isolate">
          <article>
            <BrandGlyph name="secure" size={26} />
            <h3>Organization isolation</h3>
            <p>
              Searches always include your org. Another company&rsquo;s
              documents are never in the result set.
            </p>
          </article>
          <article>
            <BrandGlyph name="workspace" size={26} />
            <h3>Team workspaces</h3>
            <p>
              Invite colleagues into a space with its own connected sources —
              ideal for projects and shared team knowledge.
            </p>
          </article>
          <article>
            <BrandGlyph name="gmail" size={28} />
            <h3>Invite-only access</h3>
            <p>
              Members sign in with a magic link. Admins invite people by email,
              and there is no open join into someone else&rsquo;s company.
            </p>
          </article>
        </div>
      </section>

      <section className="landing-close landing-wrap" aria-labelledby="how-close">
        <div className="landing-close-copy">
          <p className="landing-eyebrow">Ready when you are</p>
          <h2 id="how-close">Clear answers. Honest when it can&rsquo;t.</h2>
          <p>
            Connect your sources, invite the team, and ask workplace questions
            with citations you can verify.
          </p>
        </div>
        <div className="landing-close-actions">
          <Link href="/signup" className="button landing-cta-primary landing-close-primary">
            Request access
          </Link>
          <Link href="/" className="landing-close-link">
            Back to product overview
          </Link>
        </div>
      </section>
    </LandingShell>
  );
}
