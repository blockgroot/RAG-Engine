"use client";

import { BrandGlyph } from "@/components/BrandGlyph";
import { LandingPromptCycle } from "@/components/LandingPromptCycle";

/**
 * Live-looking product mock: chat window + orbiting source marks.
 * Motion is CSS; copy in the composer is the shared prompt cycle.
 */
export function LandingProductArt() {
  return (
    <div className="lp-stage" aria-hidden>
      <span className="lp-sat lp-sat-1">
        <BrandGlyph name="notion" size={22} />
      </span>
      <span className="lp-sat lp-sat-2">
        <BrandGlyph name="drive" size={22} />
      </span>
      <span className="lp-sat lp-sat-3">
        <BrandGlyph name="github" size={22} />
      </span>
      <span className="lp-sat lp-sat-4">
        <BrandGlyph name="gmail" size={22} />
      </span>
      <span className="lp-sat lp-sat-5">
        <BrandGlyph name="workspace" size={22} />
      </span>
      <span className="lp-sat lp-sat-6">
        <BrandGlyph name="slack" size={22} />
      </span>

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
              <BrandGlyph name="notion" size={14} />
              Leave Policy · Acme HR
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
