"use client";

/**
 * The four stages a question passes through, as one continuous run.
 *
 * Two things this fixed. Four bordered cards with arrows said these were four
 * separate things that happen to sit near each other, when a stage is a point
 * ON a line — so the line is real and a pulse travels along it. And each stage
 * carried a source icon, which was decoration standing in for an explanation:
 * the number and a full sentence do that job, and the tool marks now appear
 * once on the page, above "Works with", where they mean something.
 */

import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

const STATIONS: Array<{ title: string; body: string; feed?: BrandName[] }> = [
  {
    title: "You ask",
    body:
      "Type your question in the same box every time. There is no source to pick and no filter to set. Follow-up questions keep the earlier ones in mind, so you can narrow something down without repeating yourself.",
  },
  {
    title: "Handbook searches",
    body:
      "It reads across every tool your company has connected — documents, conversations, tickets and code — and finds the passages that address your question.",
    feed: ["notion", "slack", "drive"],
  },
  {
    title: "It checks the evidence",
    body:
      "Before answering, it judges whether what it found actually covers the question. If it does not, you are told that instead of being given the closest-looking guess.",
  },
  {
    title: "You get the answer",
    body:
      "Written from those passages only, with the document, message or file it came from listed underneath so you can open it and read the rest.",
  },
];

export function HowJourney() {
  return (
    <div className="journey" aria-label="What happens when you ask a question">
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
              <span className="journey-node-num">{`0${i + 1}`}</span>
              <span className="journey-node-ring" aria-hidden />
            </span>
            {station.feed && (
              <span className="journey-feed" aria-hidden>
                {station.feed.map((name, f) => (
                  <span
                    key={name}
                    className="journey-feed-chip"
                    style={{ ["--f" as string]: String(f) }}
                  >
                    <BrandGlyph name={name} size={14} />
                  </span>
                ))}
              </span>
            )}
            <h3>{station.title}</h3>
            <p>{station.body}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
