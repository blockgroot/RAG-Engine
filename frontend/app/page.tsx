"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BrandGlyph } from "@/components/BrandGlyph";
import { BrandMark } from "@/components/BrandMark";
import { LandingShell } from "@/components/LandingShell";
import { api } from "@/lib/api";
import { homePathFor } from "@/lib/routing";

const sources = [
  { name: "Notion", glyph: "notion" as const, className: "source-teal" },
  { name: "Drive", glyph: "drive" as const, className: "source-yellow" },
  { name: "Slack", glyph: "slack" as const, className: "source-blue" },
  { name: "Linear", glyph: "linear" as const, className: "source-ink" },
  { name: "GitHub", glyph: "github" as const, className: "source-coral" },
];

export default function RootPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.me().then((me) => {
      if (!cancelled) router.replace(homePathFor(me));
    }).catch(() => {
      if (!cancelled) setReady(true);
    });
    return () => { cancelled = true; };
  }, [router]);

  if (!ready) {
    return <main className="landing"><p className="landing-loading muted">Loading...</p></main>;
  }

  return (
    <LandingShell active="home">
      <section className="home-hero landing-wrap" aria-labelledby="home-title">
        <div className="home-hero-copy">
          <p className="landing-eyebrow home-eyebrow"><span /> Your team&apos;s knowledge, in reach</p>
          <h1 id="home-title">Ask once.<br /><em>Move faster.</em></h1>
          <p className="home-hero-lead">Handbook brings your company&apos;s docs, conversations, work, and code into one calm place to ask questions.</p>
          <div className="home-actions">
            <Link href="/signup" className="button home-primary">Request access <span aria-hidden>↗</span></Link>
            <Link href="/how-it-works" className="home-text-link">See how it works <span aria-hidden>→</span></Link>
          </div>
          <p className="home-trust"><BrandGlyph name="secure" size={16} /> Answers stay inside your connected sources.</p>
        </div>

        <div className="home-hero-art" aria-label="A preview of asking Handbook a question">
          <div className="home-art-orbit home-art-orbit-one" />
          <div className="home-art-orbit home-art-orbit-two" />
          <div className="home-art-pulse" />
          <div className="home-art-source home-art-source-a"><BrandGlyph name="notion" size={20} /></div>
          <div className="home-art-source home-art-source-b"><BrandGlyph name="slack" size={20} /></div>
          <div className="home-art-source home-art-source-c"><BrandGlyph name="github" size={20} /></div>
          <div className="home-art-window">
            <div className="home-art-topline"><span className="home-art-live" /> Handbook <span>Today, 10:42</span></div>
            <div className="home-art-question">What did we decide about the launch?</div>
            <div className="home-art-answer">
              <div className="home-art-answer-mark"><BrandMark /></div>
              <div><strong>Here&apos;s the short version.</strong><p>The team moved the launch to Thursday. Maya will share the final checklist in Slack.</p></div>
            </div>
            <div className="home-art-source-line"><span /> Grounded in Slack <b>↗</b></div>
          </div>
        </div>
      </section>

      <section className="home-source-rail" aria-label="Connect your work tools">
        <div className="landing-wrap home-source-inner">
          <span className="home-source-label">Works with the tools your team already uses</span>
          <div className="home-source-list">
            {sources.map((source) => <span className={`home-source ${source.className}`} key={source.name}><BrandGlyph name={source.glyph} size={18} /> {source.name}</span>)}
          </div>
        </div>
      </section>

      <section className="home-section landing-wrap" aria-labelledby="home-value-title">
        <div className="home-section-intro"><p className="landing-eyebrow">A better way to find the answer</p><h2 id="home-value-title">Less hunting.<br /><span>More doing.</span></h2><p>Ask in plain English. Handbook finds the right context, checks it, and gives you the useful part without the digging.</p></div>
        <div className="home-value-lines">
          <article><span className="home-line-number">01</span><div><h3>Everything your team knows</h3><p>Docs, messages, tickets, and code stay connected, so answers do not get lost between tabs.</p></div><span className="home-line-mark"><BrandGlyph name="document" size={22} /></span></article>
          <article><span className="home-line-number">02</span><div><h3>Answers you can trust</h3><p>Every answer comes from your own sources. If the evidence is not there, Handbook says so.</p></div><span className="home-line-mark"><BrandGlyph name="secure" size={22} /></span></article>
          <article><span className="home-line-number">03</span><div><h3>Updates that find you</h3><p>Set a report once and keep up with what changed, without another meeting or reminder.</p></div><span className="home-line-mark"><BrandGlyph name="schedule" size={22} /></span></article>
        </div>
      </section>

      <section className="home-split landing-wrap" aria-labelledby="home-spaces-title">
        <div className="home-split-visual"><div className="home-radar"><span /><span /><span /><b><BrandMark /></b></div></div>
        <div className="home-split-copy"><p className="landing-eyebrow">Made for real teams</p><h2 id="home-spaces-title">One shared brain.<br /><em>Clear boundaries.</em></h2><p>Create spaces for projects, invite the right people, and keep every answer scoped to the content that matters.</p><Link href="/how-it-works" className="home-text-link">Take the tour <span aria-hidden>→</span></Link></div>
      </section>

      <section className="home-final landing-wrap" aria-labelledby="home-final-title"><div className="home-final-mark"><BrandMark /></div><p className="landing-eyebrow">Ready when your team is</p><h2 id="home-final-title">Give every question<br /><em>a shorter path.</em></h2><Link href="/signup" className="button home-primary">Request access <span aria-hidden>↗</span></Link></section>
    </LandingShell>
  );
}
