"use client";

/**
 * The four stations a question passes through, as one continuous run.
 *
 * This replaced four bordered cards with arrows between them. The cards were
 * the problem: a step in a journey is a POINT ON A LINE, and drawing each one
 * as its own container said the opposite — four separate things that happen to
 * sit near each other. Here the line is real, it runs behind every station,
 * and a pulse travels along it so the direction is shown rather than labelled.
 *
 * The pulse is decoration and stops for `prefers-reduced-motion`; the stations
 * are readable with no animation at all.
 */

import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

const STATIONS: Array<{
  glyph: BrandName;
  title: string;
  body: string;
  feed?: BrandName[];
}> = [
  {
    glyph: "sendgrid",
    title: "You ask",
    body: "In your own words. Follow-ups work too, so you can keep digging.",
  },
  {
    glyph: "workspace",
    title: "It looks",
    body: "Through everything your company has connected, and nowhere else.",
    feed: ["notion", "slack", "drive"],
  },
  {
    glyph: "secure",
    title: "It checks",
    body: "If your documents don’t cover it, it says so instead of guessing.",
  },
  {
    glyph: "document",
    title: "You get it",
    body: "With a link to the page, message or file it came from.",
  },
];

export function HowJourney() {
  return (
    <div className="journey" aria-label="What happens when you ask">
      <div className="journey-rail" aria-hidden>
        <span className="journey-pulse" />
      </div>
      <ol className="journey-stations">
        {STATIONS.map((station, i) => (
          <li
            className="journey-station"
            key={station.title}
            data-reveal
            style={{ ["--i" as string]: String(i) }}
          >
            <span className="journey-node">
              <BrandGlyph name={station.glyph} size={22} />
              <span className="journey-node-ring" aria-hidden />
            </span>
            {/* The sources feeding the search, shown where they actually join
                rather than listed in a sentence somewhere else. */}
            {station.feed && (
              <span className="journey-feed" aria-hidden>
                {station.feed.map((name, f) => (
                  <span
                    key={name}
                    className="journey-feed-chip"
                    style={{ ["--f" as string]: String(f) }}
                  >
                    <BrandGlyph name={name} size={15} />
                  </span>
                ))}
              </span>
            )}
            <span className="journey-index">{`0${i + 1}`}</span>
            <h3>{station.title}</h3>
            <p>{station.body}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
