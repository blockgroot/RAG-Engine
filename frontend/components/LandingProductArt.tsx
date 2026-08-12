"use client";

import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { LandingPromptCycle } from "@/components/LandingPromptCycle";

/** Equal-spaced stations around Handbook (pentagon) — shared radius, no orbit spin. */
const SOURCES: Array<{ name: BrandName; size: number }> = [
  { name: "slack", size: 24 },
  { name: "notion", size: 24 },
  { name: "drive", size: 24 },
  { name: "github", size: 24 },
  { name: "gmail", size: 30 },
];

/**
 * Live product mock: chat window with brand marks docked around it as one system.
 */
export function LandingProductArt() {
  return (
    <div className="lp-stage" aria-hidden>
      <div className="lp-constellation">
        {SOURCES.map((item, i) => (
          <span
            key={item.name}
            className={`lp-sat${item.name === "gmail" ? " lp-sat-gmail" : ""}`}
            style={{ ["--sat-i" as string]: String(i) }}
          >
            <span className="lp-sat-face">
              <BrandGlyph name={item.name} size={item.size} />
            </span>
          </span>
        ))}
      </div>

      <div className="lp-window">
        <div className="lp-chrome">
          <span />
          <span />
          <span />
          <strong>Handbook</strong>
        </div>
        <div className="lp-tabs">
          <span className="is-on">Policies</span>
          <span>Code</span>
        </div>
        <div className="lp-thread">
          <div className="lp-bubble lp-bubble-user">
            How many sick days do full-time employees get?
          </div>
          <div className="lp-bubble lp-bubble-bot">
            <p>
              Full-time employees receive <b>10 sick days</b> each calendar year.
              Unused days do not carry over.
            </p>
          </div>
        </div>
        <div className="lp-dock">
          <LandingPromptCycle compact />
        </div>
      </div>
    </div>
  );
}
