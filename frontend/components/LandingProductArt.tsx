"use client";

import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { LandingPromptCycle } from "@/components/LandingPromptCycle";

const ORBIT: Array<{ name: BrandName; size: number }> = [
  { name: "notion", size: 22 },
  { name: "drive", size: 22 },
  { name: "github", size: 22 },
  { name: "gmail", size: 28 },
  { name: "slack", size: 22 },
];

/**
 * Live product mock: chat window with brand marks on a circular orbit.
 */
export function LandingProductArt() {
  return (
    <div className="lp-stage" aria-hidden>
      <div className="lp-orbit">
        <div className="lp-orbit-ring">
          {ORBIT.map((item, i) => (
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
            <div className="lp-cite">
              <BrandGlyph name="secure" size={16} />
              <span>Company policy</span>
            </div>
          </div>
        </div>
        <div className="lp-dock">
          <LandingPromptCycle compact />
        </div>
      </div>
    </div>
  );
}
