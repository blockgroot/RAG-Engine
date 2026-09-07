"use client";

/**
 * A sticky tracker beside the setup list, marking which step you are reading.
 *
 * The list is a single column of rows, so the right half of that section was
 * empty. This fills it with something that has a job rather than an ornament:
 * four steps, a line that fills as you scroll, and a count — so a reader can
 * see how much setup is left without scrolling back.
 *
 * It observes `[data-setup-step]` in the page rather than taking children,
 * because the copy belongs in the server-rendered page next to the rest of it.
 * With no JavaScript the first step simply stays marked and the list reads
 * exactly as it did before.
 */

import { useEffect, useState } from "react";

export function HowSetupTracker({ steps }: { steps: string[] }) {
  const [active, setActive] = useState(0);

  useEffect(() => {
    const nodes = Array.from(
      document.querySelectorAll<HTMLElement>("[data-setup-step]")
    );
    if (nodes.length === 0 || !("IntersectionObserver" in window)) return;

    const observer = new IntersectionObserver(
      (entries) => {
        // The row nearest the middle of the screen wins, so a tall row does
        // not keep the mark while the next one is clearly being read.
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible.length === 0) return;
        const index = Number(
          (visible[0].target as HTMLElement).dataset.setupStep ?? 0
        );
        setActive(index);
      },
      { rootMargin: "-40% 0px -40% 0px", threshold: [0, 0.5, 1] }
    );

    nodes.forEach((n) => observer.observe(n));
    return () => observer.disconnect();
  }, []);

  return (
    <aside className="setup-track" aria-hidden>
      <p className="setup-track-count">
        <strong>{`0${active + 1}`}</strong>
        <span>{`of 0${steps.length}`}</span>
      </p>
      <ol className="setup-track-list">
        {/* One line whose fill height follows the active index, rather than a
            border per row: a single moving indicator reads as progress. */}
        <span
          className="setup-track-fill"
          style={{
            // A 0-1 fraction, because CSS can multiply a percentage by a
            // number but not by a length. See `.setup-track-fill`.
            ["--fill" as string]:
              steps.length > 1 ? String(active / (steps.length - 1)) : "0",
          }}
        />
        {steps.map((label, i) => (
          <li
            key={label}
            className={`setup-track-step${i === active ? " is-active" : ""}${
              i < active ? " is-done" : ""
            }`}
          >
            <span className="setup-track-dot" />
            {label}
          </li>
        ))}
      </ol>
    </aside>
  );
}
