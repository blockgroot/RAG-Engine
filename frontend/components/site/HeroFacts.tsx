import type { ReactNode } from "react";
import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { ActIcon, AskIcon, ChartIcon, DeepIcon } from "@/components/ModeIcons";

const TOOLS: BrandName[] = ["notion", "drive", "slack", "linear", "github"];

const ITEMS: ReactNode[] = [
  <>
    <span className="band-logos">{TOOLS.map((t) => <i key={t}><BrandGlyph name={t} size={13} /></i>)}</span>
    5 tools connected
  </>,
  <>
    <span className="band-icons"><AskIcon /><DeepIcon /><ChartIcon /><ActIcon /></span>
    4 modes in one chat
  </>,
  <>Every answer links its source</>,
  <>Never guesses when the answer isn&rsquo;t there</>,
  <>0 actions sent without your approval</>,
  <>Each person sees only what they can open</>,
];

/**
 * Under the hero: a slow, full-width band of what Handbook is, instead of a
 * row of numbers. The list is drawn twice so the loop is seamless; it pauses
 * on hover and stands still for reduced motion.
 */
export function HeroFacts() {
  const run = (copy: number) => (
    <ul className="band-run" aria-hidden={copy > 0 || undefined}>
      {ITEMS.map((item, i) => <li key={i}>{item}</li>)}
    </ul>
  );
  return (
    <section className="site-wrap band" aria-label="At a glance">
      <div className="band-track">
        {run(0)}
        {run(1)}
      </div>
    </section>
  );
}
