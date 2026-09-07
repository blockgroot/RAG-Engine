"use client";

/**
 * Three small mocks of what an answer looks like, for the alternating rows.
 *
 * Drawn in CSS rather than shipped as images: they are three shapes at two
 * sizes, they have to recolour with the theme tokens, and an asset step for
 * that would be the only one in this frontend. Each animates once when its row
 * scrolls in — the bars grow, the reply lands, the schedule ticks — because a
 * still picture of a chart is the same wall of rectangles the copy is trying
 * to escape.
 *
 * `aria-hidden`: every one of these repeats what the prose next to it says.
 */

import { BrandGlyph } from "@/components/BrandGlyph";

export function AnswerArt() {
  return (
    <div className="art art-answer" aria-hidden>
      <div className="art-bubble art-bubble-ask">How much parental leave do I get?</div>
      <div className="art-bubble art-bubble-reply">
        <span className="art-line" style={{ width: "88%" }} />
        <span className="art-line" style={{ width: "96%" }} />
        <span className="art-line" style={{ width: "54%" }} />
        <span className="art-source">
          <BrandGlyph name="notion" size={14} />
          People handbook
        </span>
      </div>
    </div>
  );
}

const BARS = [38, 64, 52, 88, 71, 46];

export function ChartArt() {
  return (
    <div className="art art-chart" aria-hidden>
      <div className="art-chart-plot">
        {BARS.map((height, i) => (
          <span
            key={i}
            className="art-bar"
            style={{ ["--h" as string]: `${height}%`, ["--b" as string]: String(i) }}
          />
        ))}
      </div>
      <div className="art-chart-axis" />
      <span className="art-chip art-chip-float">3 commits · 9 Jul</span>
    </div>
  );
}

export function InboxArt() {
  return (
    <div className="art art-inbox" aria-hidden>
      <div className="art-mail">
        <span className="art-mail-dot" />
        <div>
          <strong>Your weekly summary</strong>
          <span className="art-line" style={{ width: "72%" }} />
          <span className="art-line" style={{ width: "84%" }} />
        </div>
      </div>
      <div className="art-schedule">
        <span className="art-tick is-on">Mon</span>
        <span className="art-tick">Tue</span>
        <span className="art-tick">Wed</span>
        <span className="art-tick">Thu</span>
        <span className="art-tick">Fri</span>
      </div>
    </div>
  );
}
