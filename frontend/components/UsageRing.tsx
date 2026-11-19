"use client";

import { useEffect, useState } from "react";

import { api, type AiUsage } from "@/lib/api";

/** "in 3 hours", "in 12 minutes": the wait is hours for a day's limit. */
function waitWords(seconds: number): string {
  if (seconds >= 5400) return `in about ${Math.ceil(seconds / 3600)} hours`;
  if (seconds >= 90) return `in about ${Math.ceil(seconds / 60)} minutes`;
  return "in a moment";
}

/**
 * How much of today's AI use is left, as a ring beside the model menu, the
 * way Claude.ai and ChatGPT show it. One budget for every model: a bigger one
 * uses it faster. Hidden when nothing limits the key that answers (your own
 * key with no limit set). Re-read when the model changes and after each answer.
 */
export function UsageRing({ model, refresh }: { model: string; refresh: unknown }) {
  const [usage, setUsage] = useState<AiUsage | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .chatUsage(model)
      .then((u) => !cancelled && setUsage(u))
      // A ring is a hint: if it cannot be read, show nothing rather than an error.
      .catch(() => !cancelled && setUsage(null));
    return () => {
      cancelled = true;
    };
  }, [model, refresh]);

  if (!usage?.limited) return null;

  const left = Math.max(0, Math.min(1, usage.left));
  const percent = Math.round(left * 100);
  const tone = left <= 0 ? "is-empty" : left < 0.2 ? "is-low" : "";
  const whose = usage.whose === "company" ? "your company’s" : "your";
  const headline =
    left <= 0
      ? usage.whose === "company" ? "Your company reached today’s AI limit" : "You’ve reached today’s AI limit"
      : `${percent}% of ${whose} AI use left today`;
  const detail =
    left <= 0
      ? `You can ask again ${waitWords(usage.retry_after)}. It refills gradually.`
      : "Bigger models use more. It refills gradually through the day.";
  // Circumference of r=8: the stroke shows what is left.
  const C = 2 * Math.PI * 8;

  return (
    <span className={`usage-ring ${tone}`} tabIndex={0} aria-label={`${headline}. ${detail}`}>
      <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden>
        <circle cx="11" cy="11" r="8" className="usage-ring-track" />
        <circle
          cx="11"
          cy="11"
          r="8"
          className="usage-ring-fill"
          strokeDasharray={`${C * left} ${C}`}
          transform="rotate(-90 11 11)"
        />
      </svg>
      <span className="usage-ring-tip" role="tooltip">
        <strong>{headline}</strong>
        <small>{detail}</small>
      </span>
    </span>
  );
}
