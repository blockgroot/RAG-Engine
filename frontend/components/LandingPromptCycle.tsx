"use client";

import { useEffect, useState } from "react";

/** Workplace questions that map to real Handbook paths (policies / code / spaces). */
export const LANDING_PROMPTS = [
  "How many sick days do full-time employees get?",
  "What's our parental leave policy?",
  "What changed in the latest checkout-api commit?",
  "Summarize yesterday's design review notes",
  "Can I expense a standing desk?",
];

/**
 * Onyx-style cycling prompt: types into a fake composer so the hero shows
 * *what you actually ask*, not just a tagline. Motion is CSS/JS and stops
 * when the user prefers reduced motion.
 */
export function LandingPromptCycle({ compact = false }: { compact?: boolean }) {
  const [index, setIndex] = useState(0);
  const [typed, setTyped] = useState("");
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduceMotion(mq.matches);
    const onChange = () => setReduceMotion(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (reduceMotion) {
      setTyped(LANDING_PROMPTS[0]);
      return;
    }
    const full = LANDING_PROMPTS[index];
    setTyped("");
    let i = 0;
    const typeId = window.setInterval(() => {
      i += 1;
      setTyped(full.slice(0, i));
      if (i >= full.length) {
        window.clearInterval(typeId);
      }
    }, 28);
    const nextId = window.setTimeout(() => {
      setIndex((n) => (n + 1) % LANDING_PROMPTS.length);
    }, full.length * 28 + 2200);
    return () => {
      window.clearInterval(typeId);
      window.clearTimeout(nextId);
    };
  }, [index, reduceMotion]);

  return (
    <div className={`landing-prompt${compact ? " is-compact" : ""}`} aria-live="polite">
      {compact ? null : <span className="landing-prompt-kicker">Try asking</span>}
      <p className="landing-prompt-line">
        <span className="landing-prompt-text">{typed}</span>
        {!reduceMotion ? <span className="landing-prompt-caret" aria-hidden /> : null}
      </p>
    </div>
  );
}
