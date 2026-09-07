import type { Metadata } from "next";
import Link from "next/link";
import { BrandGlyph } from "@/components/BrandGlyph";
import { BrandMark } from "@/components/BrandMark";
import { LandingShell } from "@/components/LandingShell";

export const metadata: Metadata = {
  title: "How it works",
  description: "See how Handbook turns a workplace question into a clear answer.",
};

const steps = [
  { number: "01", title: "Ask naturally", text: "Type the question the way you would ask a teammate. No special words or search tricks needed." },
  { number: "02", title: "Handbook finds the thread", text: "It looks across the sources your team connected and brings the most useful context together." },
  { number: "03", title: "Get the answer", text: "You get a clear reply with its source. If the answer is not there, Handbook will not make one up." },
];

export default function HowItWorksPage() {
  return (
    <LandingShell active="how">
      <section className="how-new-hero landing-wrap" aria-labelledby="how-new-title">
        <div><p className="landing-eyebrow">A simpler way to work</p><h1 id="how-new-title">Ask. Find.<br /><em>Keep moving.</em></h1><p>Handbook turns scattered team knowledge into an answer you can use, without making you learn another system.</p><Link href="/signup" className="button home-primary">Try Handbook <span aria-hidden>↗</span></Link></div>
        <div className="how-new-hero-art" aria-label="Handbook finds an answer from connected sources">
          <div className="how-art-label how-art-label-one"><BrandGlyph name="notion" size={17} /> Notes</div><div className="how-art-label how-art-label-two"><BrandGlyph name="slack" size={17} /> Conversations</div><div className="how-art-label how-art-label-three"><BrandGlyph name="linear" size={17} /> Work</div><div className="how-art-core"><BrandMark /><span>One clear answer</span></div><i className="how-art-path path-one" /><i className="how-art-path path-two" /><i className="how-art-path path-three" />
        </div>
      </section>

      <section className="how-new-steps landing-wrap" aria-labelledby="how-steps-title"><div className="how-new-section-heading"><p className="landing-eyebrow">The whole experience</p><h2 id="how-steps-title">Three simple moves.</h2></div><div className="how-step-list">{steps.map((step, index) => <article className="how-step-row" key={step.number} style={{ ["--step-delay" as string]: `${index * 120}ms` }}><span>{step.number}</span><div><h3>{step.title}</h3><p>{step.text}</p></div><b aria-hidden>{index === 0 ? "↗" : index === 1 ? "⌁" : "✓"}</b></article>)}</div></section>

      <section className="how-new-sources landing-wrap" aria-labelledby="how-sources-title"><div className="how-sources-copy"><p className="landing-eyebrow">Your tools, together</p><h2 id="how-sources-title">One question.<br /><em>The right place.</em></h2><p>Connect the tools your team already trusts. Handbook brings them into one place, while keeping each source and workspace in its proper lane.</p></div><div className="how-source-stage"><div className="how-source-line" /><div className="how-source-orbit"><span className="orbit-source orbit-notion"><BrandGlyph name="notion" size={20} /></span><span className="orbit-source orbit-drive"><BrandGlyph name="drive" size={20} /></span><span className="orbit-source orbit-slack"><BrandGlyph name="slack" size={20} /></span><span className="orbit-source orbit-github"><BrandGlyph name="github" size={20} /></span><span className="orbit-core"><BrandMark /></span></div></div></section>

      <section className="how-new-trust landing-wrap" aria-labelledby="how-trust-title"><div><p className="landing-eyebrow">Designed for confidence</p><h2 id="how-trust-title">Useful when it knows.<br /><em>Honest when it doesn&apos;t.</em></h2></div><div className="how-trust-notes"><p><BrandGlyph name="secure" size={20} /><span><strong>Your content stays yours.</strong> Every search is limited to your organization and workspace.</span></p><p><BrandGlyph name="document" size={20} /><span><strong>Sources stay visible.</strong> See where an answer came from, not just what it says.</span></p><p><BrandGlyph name="private" size={20} /><span><strong>No confident guessing.</strong> When your sources do not cover it, Handbook tells you.</span></p></div></section>

      <section className="how-new-final landing-wrap" aria-labelledby="how-final-title"><div className="how-final-orb"><BrandMark /></div><p className="landing-eyebrow">Start with one question</p><h2 id="how-final-title">Your team already<br /><em>has the answers.</em></h2><Link href="/signup" className="button home-primary">Request access <span aria-hidden>↗</span></Link></section>
    </LandingShell>
  );
}
