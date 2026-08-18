import type { Metadata } from "next";
import Link from "next/link";
import { BrandGlyph } from "@/components/BrandGlyph";
import { LandingShell } from "@/components/LandingShell";

export const metadata: Metadata = {
  title: "How it works",
  description:
    "How Handbook turns a workplace question into a grounded answer from your connected sources.",
};

export default function HowItWorksPage() {
  return (
    <LandingShell active="how">
      <section className="how-hero landing-wrap" aria-labelledby="how-title">
        <p className="landing-eyebrow">Product walkthrough</p>
        <h1 id="how-title" className="how-title">
          From question to grounded answer.
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
          <p>Search your org&rsquo;s connected content — never another company&rsquo;s.</p>
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
          <p>The reply stays on retrieved text — or a clear fallback if it can&rsquo;t.</p>
        </article>
      </div>

      <section className="landing-section landing-wrap" aria-labelledby="paths-title">
        <div className="landing-section-head">
          <div>
            <p className="landing-eyebrow">Three paths</p>
            <h2 id="paths-title" className="landing-section-title">
              Policies from your docs. Chat from Slack. Code from GitHub.
            </h2>
          </div>
          <p className="landing-section-lead">
            Choose Policies, Slack, or Code in chat. You decide the path —
            Handbook doesn&rsquo;t guess which agent to run.
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
              <li>Answers stay grounded in that content, or refuse when they can&rsquo;t.</li>
            </ol>
          </article>

          <article className="how-path">
            <header className="how-path-head">
              <BrandGlyph name="slack" size={28} />
              <div>
                <strong>Slack</strong>
                <span>Connected channels · synced threads</span>
              </div>
            </header>
            <ol>
              <li>Admin picks specific channels and runs a sync.</li>
              <li>Threads are stored under your organization — not live-searched.</li>
              <li>Questions retrieve those channels, then pass a confidence check.</li>
              <li>Answers stay grounded in that chat history, or refuse when they can&rsquo;t.</li>
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
            <BrandGlyph name="sendgrid" size={28} />
            <h3>Invite-only access</h3>
            <p>
              Members sign in with a magic link. Admins invite people by email,
              and there is no open join into someone else&rsquo;s company.
            </p>
          </article>
        </div>
      </section>

      <section className="landing-section landing-wrap how-why" aria-labelledby="why-title">
        <div className="how-why-hero">
          <p className="landing-eyebrow">Why Handbook</p>
          <h2 id="why-title">Why teams connect — and what you get.</h2>
          <p>
            Workplace questions should land on your own policies, Slack
            threads, and repos — not a model&rsquo;s guess. Handbook keeps
            every answer grounded, scoped, and honest when the content
            doesn&rsquo;t cover it.
          </p>
        </div>

        <ol className="how-promise" aria-label="Why connect with Handbook">
          <li className="how-promise-item" style={{ ["--i" as string]: "0" }}>
            <span className="how-promise-index" aria-hidden>01</span>
            <div className="how-promise-body">
              <strong>Stop hunting across tools</strong>
              <p>
                One place for leave, benefits, expenses, channel decisions,
                READMEs, and recent commits — instead of tab-hopping.
              </p>
            </div>
            <span className="how-promise-tag">Ask once</span>
          </li>
          <li className="how-promise-item" style={{ ["--i" as string]: "1" }}>
            <span className="how-promise-index" aria-hidden>02</span>
            <div className="how-promise-body">
              <strong>Trust answers you can rely on</strong>
              <p>
                Answers come only from your connected content. Weak evidence
                means a clear refusal — never a confident invention.
              </p>
            </div>
            <span className="how-promise-tag">Grounded</span>
          </li>
          <li className="how-promise-item" style={{ ["--i" as string]: "2" }}>
            <span className="how-promise-index" aria-hidden>03</span>
            <div className="how-promise-body">
              <strong>Keep knowledge inside the company</strong>
              <p>
                Every search is org-scoped. Admins invite by email; other
                tenants never see your content.
              </p>
            </div>
            <span className="how-promise-tag">Isolated</span>
          </li>
        </ol>

        <div className="how-included" aria-labelledby="offer-title">
          <div className="how-included-copy">
            <p className="landing-eyebrow">What you get</p>
            <h3 id="offer-title">Connect once. Ask with confidence.</h3>
            <p className="how-included-lead">
              Sync Notion, Drive, or Slack, look up GitHub live, invite the
              team with a magic link, and get grounded answers — or a clear
              fallback when the connected content doesn&rsquo;t cover it.
            </p>
            <p className="how-included-for">
              Built for <em>People &amp; ops</em>, <em>Engineering</em>, and{" "}
              <em>Admins</em>.
            </p>
          </div>

          <div className="how-constellation" aria-hidden>
            <svg className="how-constellation-links" viewBox="0 0 280 260" fill="none">
              <path
                className="how-constellation-path"
                d="M140 130 L70 48 M140 130 L140 42 M140 130 L210 48 M140 130 L58 175 M140 130 L222 175 M140 130 L140 222"
              />
            </svg>
            <div className="how-constellation-core">
              <span>Ask</span>
            </div>
            <span className="how-constellation-node how-node-a" style={{ ["--d" as string]: "0s" }}>
              <BrandGlyph name="notion" size={20} />
              <em>Notion</em>
            </span>
            <span className="how-constellation-node how-node-b" style={{ ["--d" as string]: "0.15s" }}>
              <BrandGlyph name="drive" size={20} />
              <em>Drive</em>
            </span>
            <span className="how-constellation-node how-node-c" style={{ ["--d" as string]: "0.3s" }}>
              <BrandGlyph name="github" size={20} />
              <em>GitHub</em>
            </span>
            <span className="how-constellation-node how-node-d" style={{ ["--d" as string]: "0.45s" }}>
              <BrandGlyph name="sendgrid" size={22} />
              <em>Magic link</em>
            </span>
            <span className="how-constellation-node how-node-e" style={{ ["--d" as string]: "0.6s" }}>
              <BrandGlyph name="slack" size={20} />
              <em>Slack</em>
            </span>
            <span className="how-constellation-node how-node-f" style={{ ["--d" as string]: "0.75s" }}>
              <BrandGlyph name="secure" size={20} />
              <em>Grounded</em>
            </span>
          </div>
        </div>
      </section>

      <section className="landing-section landing-wrap how-craft" aria-labelledby="craft-title">
        <div className="how-craft-head">
          <p className="landing-eyebrow">How it answers</p>
          <h2 id="craft-title" className="landing-section-title">
            Built for how people actually ask.
          </h2>
          <p className="landing-section-lead">
            Beyond connect and sync — the reply path is designed to understand
            intent, stay clear, and stay honest.
          </p>
        </div>

        <ol className="how-craft-zigzag" aria-label="Answer experience">
          <li className="how-craft-step" style={{ ["--i" as string]: "0" }}>
            <span className="how-craft-num" aria-hidden>01</span>
            <div className="how-craft-card">
              <span className="how-craft-kicker">Intent</span>
              <strong>Understands intent</strong>
              <p>
                Follow-ups and shorthand become a clear standalone question before
                retrieval — so “what about part-timers?” still finds the right policy.
              </p>
            </div>
          </li>
          <li className="how-craft-step" style={{ ["--i" as string]: "1" }}>
            <span className="how-craft-num" aria-hidden>02</span>
            <div className="how-craft-card">
              <span className="how-craft-kicker">Tone</span>
              <strong>Answers with care</strong>
              <p>
                Clear, empathetic, workplace-ready language — what your documents
                support, without fluff or invented certainty.
              </p>
            </div>
          </li>
          <li className="how-craft-step" style={{ ["--i" as string]: "2" }}>
            <span className="how-craft-num" aria-hidden>03</span>
            <div className="how-craft-card">
              <span className="how-craft-kicker">Recovery</span>
              <strong>Recovers when wording misses</strong>
              <p>
                Typos and alternate phrasings get a bounded second look — so a near
                miss doesn&rsquo;t become a dead end.
              </p>
            </div>
          </li>
          <li className="how-craft-step" style={{ ["--i" as string]: "3" }}>
            <span className="how-craft-num" aria-hidden>04</span>
            <div className="how-craft-card">
              <span className="how-craft-kicker">Memory</span>
              <strong>Remembers the conversation</strong>
              <p>
                Recent turns stay in context; older ones fold into a running summary
                so long chats stay coherent without starting over.
              </p>
            </div>
          </li>
          <li className="how-craft-step" style={{ ["--i" as string]: "4" }}>
            <span className="how-craft-num" aria-hidden>05</span>
            <div className="how-craft-card">
              <span className="how-craft-kicker">Honesty</span>
              <strong>Stays honest when it can&rsquo;t</strong>
              <p>
                Weak evidence means a clear fallback — never a confident guess.
                External web answers are labelled when they apply.
              </p>
            </div>
          </li>
        </ol>
      </section>

      <section
        className="landing-close landing-close-simple landing-wrap"
        aria-labelledby="how-close"
      >
        <div className="landing-close-panel">
          <div className="landing-close-copy">
            <p className="landing-eyebrow">Ready when you are</p>
            <h2 id="how-close">Clear answers. Honest when it can&rsquo;t.</h2>
            <p className="landing-close-lead">
              Request access to try Handbook with your own company sources.
            </p>
            <div className="landing-close-actions">
              <Link href="/signup" className="button landing-cta-primary landing-close-primary">
                Request access
              </Link>
              <Link href="/" className="landing-close-link">
                Back to product overview
              </Link>
            </div>
          </div>
        </div>
      </section>
    </LandingShell>
  );
}
