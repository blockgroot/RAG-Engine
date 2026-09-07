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

const STATIONS: Array<{ title: string; body: string }> = [
  {
    title: "You ask",
    body:
      "Type it in the same box every time. There is no source to pick and no filter to set, and a follow-up keeps the earlier one in mind.",
  },
  {
    title: "Handbook searches",
    body:
      "It reads every tool your company connected: documents, chats, tickets and code. It keeps the passages that address your question.",
  },
  {
    title: "It checks the evidence",
    body:
      "It judges whether what it found actually covers the question. If it does not, you are told so rather than handed the closest guess.",
  },
  {
    title: "You get the answer",
    body:
      "Written from those passages only, with the document, message or file it came from listed underneath so you can read the rest of it.",
  },
];

export function HowJourney() {
  return (
    <div className="journey" aria-label="What happens when you ask a question">
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
            </span>
            <h3>{station.title}</h3>
            <p>{station.body}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
