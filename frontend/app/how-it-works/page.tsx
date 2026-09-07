import type { Metadata } from "next";
import Link from "next/link";

import { BrandGlyph } from "@/components/BrandGlyph";
import { AnswerArt, ChartArt, InboxArt } from "@/components/HowAnswerArt";
import { HowJourney } from "@/components/HowJourney";
import { HowSetupTracker } from "@/components/HowSetupTracker";
import { LandingShell } from "@/components/LandingShell";
import { RevealOnScroll, ScrollRail } from "@/components/Reveal";

export const metadata: Metadata = {
  title: "How it works",
  description:
    "What Handbook does with your connected tools, what you can ask it for, and what you need to set up.",
};

/**
 * The explanatory page. It carries the DEFINITIONS and the detail; the landing
 * page carries the short version and must not repeat these paragraphs.
 *
 * Two standing rules for the copy here:
 *  - Plain, complete sentences that explain a function. No aphorisms, no
 *    winks, no "not fourteen search results" — a reader on this page is trying
 *    to understand what the product does, and a joke is a sentence that does
 *    not answer them.
 *  - Describe the outcome, never the machinery. "Grounded", "org-scoped" and
 *    "the confidence gate" are real and belong in CLAUDE.md.
 */
export default function HowItWorksPage() {
  return (
    <LandingShell active="how">
      <RevealOnScroll />
      <ScrollRail />

      <section className="how-hero landing-wrap has-spotlight" aria-labelledby="how-title">
        <span className="how-hero-aura" aria-hidden />
        <p className="landing-eyebrow" data-reveal>
          How it works
        </p>
        <h1 id="how-title" className="how-title" data-reveal style={{ ["--i" as string]: "1" }}>
          From a question to an answer you can check.
        </h1>
        <p className="how-lead" data-reveal style={{ ["--i" as string]: "2" }}>
          Handbook connects to the tools your company already keeps its work in.
          Once a tool is connected, anyone on your team can ask questions about
          what is inside it in ordinary language, and every answer names the
          document it came from. This page explains each part of that, and what you
          need to set up first.
        </p>
        <div className="how-hero-tools" data-reveal style={{ ["--i" as string]: "3" }}>
          <span className="how-hero-tools-label">Works with</span>
          <span className="how-hero-tools-row">
            {(["notion", "drive", "slack", "linear", "github"] as const).map((name, i) => (
              <span
                key={name}
                className="how-hero-tool"
                style={{ ["--f" as string]: String(i) }}
              >
                <BrandGlyph name={name} size={22} />
              </span>
            ))}
          </span>
        </div>
      </section>

      <section className="show-band landing-wrap" aria-labelledby="journey-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">The four stages</p>
          <h2 id="journey-title" className="show-title">
            What happens when you ask a question.
          </h2>
          <p className="show-lead">
            Every question goes through the same four stages. Only the first one
            involves you.
          </p>
        </div>
        <HowJourney />
      </section>

      <section className="show-band show-band-tint" aria-labelledby="ask-title">
        <div className="landing-wrap">
          <div className="show-head" data-reveal>
            <p className="landing-eyebrow">What you can ask for</p>
            <h2 id="ask-title" className="show-title">
              Three kinds of answer.
            </h2>
            <p className="show-lead">
              You do not choose between them in advance. Ask for a figure and you get
              a chart; ask a question and you get a written answer; ask to be kept
              updated and you get an email. Handbook decides from how you phrase it.
            </p>
          </div>

          <div className="feat" data-reveal>
            <div className="feat-copy">
              <span className="feat-kicker">A written answer</span>
              <h3>For anything held in a document</h3>
              <p>
                Ask something like &ldquo;how much parental leave do I get?&rdquo;
                and you get a short written answer drawn from your own policies,
                notes or conversations.
              </p>
              <p>
                Underneath it, Handbook lists the document the answer came from,
                which application it lives in, and who last edited it. That is there
                so you can open the source and confirm it yourself, or check whether
                the page is current before you rely on it.
              </p>
            </div>
            <div className="feat-art">
              <AnswerArt />
            </div>
          </div>

          <div className="feat feat-flip" data-reveal>
            <div className="feat-copy">
              <span className="feat-kicker">A chart</span>
              <h3>For anything that is a count over time</h3>
              <p>
                Ask for something like &ldquo;commits by author this quarter&rdquo;
                and Handbook counts the activity it has recorded from your connected
                tools and draws it.
              </p>
              <p>
                The figures are counted from that recorded activity rather than
                estimated, and hovering any bar or slice lists the individual items
                it is made of &mdash; who did what, and on which day. Charts are
                available for documents, conversations, tickets and code.
              </p>
            </div>
            <div className="feat-art">
              <ChartArt />
            </div>
          </div>

          <div className="feat" data-reveal>
            <div className="feat-copy">
              <span className="feat-kicker">A scheduled report</span>
              <h3>For the question you would otherwise ask every week</h3>
              <p>
                Describe what you want to keep track of in your own words, choose
                daily, weekly or monthly, and Handbook emails you a summary on that
                schedule.
              </p>
              <p>
                Each report covers only what changed since the last one was sent, so
                nothing repeats and nothing is skipped. Every item in it links to the
                message, ticket or commit it describes, and the full report is also
                kept in Handbook so you can read it again later.
              </p>
            </div>
            <div className="feat-art">
              <InboxArt />
            </div>
          </div>
        </div>
      </section>

      <section className="show-band landing-wrap" aria-labelledby="trust-title">
        <div className="show-head" data-reveal>
          <p className="landing-eyebrow">How answers stay accurate</p>
          <h2 id="trust-title" className="show-title">
            What Handbook does when it isn&rsquo;t sure.
          </h2>
          <p className="show-lead">
            An answer about your own company is only useful if it is right, so
            Handbook is built to stop rather than fill a gap. Three things follow
            from that.
          </p>
        </div>

        <ol className="show-flow" aria-label="How answers stay accurate">
          <li className="show-step" data-reveal style={{ ["--i" as string]: "0" }}>
            <h3>It answers only from your material</h3>
            <p>
              Answers are written from the documents, messages and tickets your
              company connected. Handbook does not answer from general knowledge,
              and it does not search the public web unless you ask it to look up
              something external by name.
            </p>
          </li>
          <li className="show-step" data-reveal style={{ ["--i" as string]: "1" }}>
            <h3>It tells you when nothing covers the question</h3>
            <p>
              If your connected tools do not contain the answer, Handbook says so
              and suggests what to ask instead. It is designed to give you a clear
              &ldquo;this is not covered&rdquo; rather than an answer assembled from
              loosely related pages.
            </p>
          </li>
          <li className="show-step" data-reveal style={{ ["--i" as string]: "2" }}>
            <h3>It shows you where every answer came from</h3>
            <p>
              Each answer carries the document behind it and links straight to it,
              and reports list every item they were built from. You can always check
              the original rather than taking the summary on trust.
            </p>
          </li>
        </ol>
      </section>

      <section className="show-band show-band-tint" aria-labelledby="setup-title">
        <div className="landing-wrap">
          <div className="show-head" data-reveal>
            <p className="landing-eyebrow">What you set up</p>
            <h2 id="setup-title" className="show-title">
              Four decisions, made once.
            </h2>
            <p className="show-lead">
              Only the first is required. None of them need a developer, and each can
              be changed later without affecting the others.
            </p>
          </div>

          <div className="setup">
            <ul className="show-list">
              <li className="show-item" data-reveal data-setup-step="0">
                <span className="show-item-num">01</span>
                <div>
                  <h3>Connect your tools</h3>
                  <p>
                    Someone with admin access signs in to Notion, Google Drive,
                    Slack, Linear, GitHub or Google Forms. Handbook then reads what
                    that account can already see &mdash; for Drive you choose a
                    folder, for Slack you choose channels, and for GitHub you choose
                    repositories. Nothing is uploaded or copied anywhere.
                  </p>
                </div>
              </li>
              <li className="show-item" data-reveal data-setup-step="1">
                <span className="show-item-num">02</span>
                <div>
                  <h3>Invite your team</h3>
                  <p>
                    Admins invite people by email address. Everyone signs in with a
                    link sent to that address, so there is no additional password to
                    manage, and nobody can add themselves to your company.
                  </p>
                </div>
              </li>
              <li className="show-item" data-reveal data-setup-step="2">
                <span className="show-item-num">03</span>
                <div>
                  <h3>Add spaces, if you need them</h3>
                  <p>
                    A space is a smaller area inside your company with its own
                    connected tools and its own members &mdash; useful for a project
                    or a department whose material should not be company-wide.
                    Questions asked in a space are answered only from that
                    space&rsquo;s tools.
                  </p>
                </div>
              </li>
              <li className="show-item" data-reveal data-setup-step="3">
                <span className="show-item-num">04</span>
                <div>
                  <h3>Choose an AI model, or use ours</h3>
                  <p>
                    If your company already has an account with OpenAI, Anthropic,
                    Google or another provider, an admin can add that key and your
                    team can select the model when they ask. Handbook tests the key
                    before saving it. If you skip this step, everything works on the
                    built-in models.
                  </p>
                </div>
              </li>
            </ul>

            <HowSetupTracker
              steps={[
                "Connect your tools",
                "Invite your team",
                "Add spaces",
                "Choose a model",
              ]}
            />
          </div>
        </div>
      </section>

      <section className="show-band vow landing-wrap" aria-labelledby="privacy-title">
        <span className="vow-aura" aria-hidden />
        <p className="landing-eyebrow" data-reveal>
          Your data
        </p>
        <h2 id="privacy-title" className="vow-title" data-reveal style={{ ["--i" as string]: "1" }}>
          We never train on your data,
          <span className="show-title-accent"> and never share it.</span>
        </h2>
        <p className="vow-lead" data-reveal style={{ ["--i" as string]: "2" }}>
          To answer questions, Handbook keeps a searchable copy of the documents you
          connect. That copy exists for one purpose: answering your own
          team&rsquo;s questions. It is not used to train AI models, it is not sold
          or shared, and it is deleted when you disconnect the tool or close your
          account.
        </p>
        <ul className="vow-points">
          <li data-reveal style={{ ["--i" as string]: "3" }}>
            <strong>Never used for training</strong>
            Your documents are not used to improve any AI model, ours or a
            provider&rsquo;s. Where a request goes to an external model, it is sent
            with training explicitly refused.
          </li>
          <li data-reveal style={{ ["--i" as string]: "4" }}>
            <strong>Only your company can reach it</strong>
            Every question is answered from your own company&rsquo;s material. No
            other company using Handbook can search it, and a space&rsquo;s material
            stays inside that space.
          </li>
          <li data-reveal style={{ ["--i" as string]: "5" }}>
            <strong>Survey answers are treated differently</strong>
            Survey responses are never made searchable and are not stored. Handbook
            keeps only the sentiment it read, with no name attached, and a topic with
            fewer than five responses is never charted.
          </li>
        </ul>
      </section>

      <section className="how-close landing-wrap" aria-labelledby="how-close">
        <div className="how-close-panel has-spotlight" data-reveal>
          <p className="landing-eyebrow">Getting started</p>
          <h2 id="how-close">Connect one tool and ask one question.</h2>
          <p className="how-close-lead">
            The quickest way to judge Handbook is to connect a single tool and ask it
            something you already know the answer to. Setup takes a few minutes, and
            you can disconnect a tool at any time.
          </p>
          <div className="how-close-actions">
            <Link href="/signup" className="button landing-cta-primary">
              Request access
            </Link>
            <Link href="/" className="landing-close-link">
              Back to the overview
            </Link>
          </div>
        </div>
      </section>
    </LandingShell>
  );
}
