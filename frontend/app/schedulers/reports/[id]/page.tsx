"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { AnswerText } from "@/components/AnswerText";
import { AppShell } from "@/components/AppShell";
import { useMe } from "@/lib/useMe";
import { api, ReportDetail } from "@/lib/api";

const FREQUENCY_LABEL: Record<string, string> = {
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly",
};

/** Names the report's window ("Week of 18 August" only for a week). */
const WINDOW_NOUN: Record<string, string> = {
  daily: "",
  weekly: "Week of",
  monthly: "Period",
};

const PROVIDER_LABEL: Record<string, string> = {
  github: "GitHub",
  slack: "Slack",
  linear: "Linear",
};

/** "Mon 18 – Mon 25 Aug": the window covered, not when it was sent. */
function windowLabel(start: string, end: string): string {
  const from = new Date(start);
  const to = new Date(end);
  const short: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric" };
  const withMonth: Intl.DateTimeFormatOptions = {
    weekday: "short",
    day: "numeric",
    month: "long",
  };
  // Drop the repeated month when both ends share one: "Mon 18 – Mon 25 August".
  const sameMonth = from.getMonth() === to.getMonth() && from.getFullYear() === to.getFullYear();
  return `${from.toLocaleDateString([], sameMonth ? short : withMonth)} – ${to.toLocaleDateString(
    [],
    withMonth
  )}`;
}

/** "25 August 2026 at 3:05 pm" — hour12 forced, never the locale's guess. */
function sentLabel(iso: string): string {
  const when = new Date(iso);
  const date = when.toLocaleDateString([], {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
  const time = when.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
  return `${date} at ${time}`;
}

// A report item's URL comes from the source's own API, not the model, but a
// `javascript:` or `data:` value in a source record would still run on click.
// Only http(s) becomes a link; anything else renders as plain text.
function isWebUrl(url: string | null | undefined): url is string {
  return !!url && /^https?:\/\//i.test(url);
}

/** One activity item as who / where / when / what; strips the Slack chunk
 *  header the meta already repeats. */
function tidyItem(item: { summary: string; meta?: string | null }) {
  const meta = item.meta || "";
  const cut = meta.lastIndexOf(" · ");
  const when = cut >= 0 ? meta.slice(cut + 3) : "";
  let where = cut >= 0 ? meta.slice(0, cut) : meta;
  let who = "";
  let text = item.summary;
  const slack = /^Channel:\s*(#[\w.-]+)\s*(?:\[\d{1,2}:\d{2}\])?\s*([^:\n]{1,60}):\s*([\s\S]*)$/.exec(text);
  if (slack) {
    [, where, who, text] = slack;
  }
  text = text.replace(/\*+/g, "").replace(/\s+/g, " ").trim().replace(/^(?:\d+\.|[•\-–])\s+/, "");
  return { who, where, when, text };
}

const ITEMS_SHOWN = 6;

export default function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me, loading } = useMe();
  const [report, setReport] = useState<ReportDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [allItems, setAllItems] = useState(false);

  useEffect(() => {
    if (!me) return;
    api
      .getReport(id)
      .then(setReport)
      .catch(() =>
        // 404 covers both "gone" and "not yours" by design — there is nothing
        // to learn from the difference, so the copy does not speculate.
        setError("That report could not be found. It may have been deleted.")
      );
  }, [me, id]);

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  if (error) {
    return (
      <AppShell me={me}>
        <main className="page stack">
          <div className="banner banner-warn" role="alert">
            {error}
          </div>
          <Link className="button button-secondary" href="/schedulers">
            Back to scheduled reports
          </Link>
        </main>
      </AppShell>
    );
  }

  if (!report) {
    return (
      <AppShell me={me}>
        <main className="page">
          <p className="muted">Loading report…</p>
        </main>
      </AppShell>
    );
  }

  const provider = PROVIDER_LABEL[report.provider] ?? report.provider;

  return (
    <AppShell me={me}>
      {/* page-wide, like every other studio page: `.page` is 640px, which
          squeezes the studio header's art column. The prose keeps its own
          68ch measure via .report-body, so the wider shell costs nothing. */}
      <main className="reports-page">
        <Link href="/schedulers" className="space-back">
          ← All reports
        </Link>
        <header className="space-hero">
          <div className="space-hero-copy">
            <span className="ask-scope">
              <span className="ask-scope-dot" aria-hidden />
              {FREQUENCY_LABEL[report.frequency] ?? report.frequency} report
            </span>
            <h1>{report.title}</h1>
            <p className="ask-hello-copy">
              {`${WINDOW_NOUN[report.frequency] ?? "Period"} ${windowLabel(
                report.window_start,
                report.window_end
              )} · generated ${sentLabel(report.created_at)}`}
            </p>
            <div className="space-hero-chips">
              <span className="studio-chip">{provider}</span>
              <span className="studio-chip">{report.space_name ?? "Company-wide"}</span>
              {report.item_count > 0 ? (
                <span className="studio-chip studio-chip-ok">
                  {report.item_count} item{report.item_count === 1 ? "" : "s"}
                </span>
              ) : (
                <span className="studio-chip">Quiet period</span>
              )}
            </div>
          </div>
        </header>

        <section className="studio-panel report-body" aria-label="Report">
          {report.item_count === 0 ? (
            // A quiet period is a RESULT: say what was read and when (no model).
            <div className="report-quiet">
              <h2>Nothing happened in this period</h2>
              <p className="muted">
                No {provider} activity was recorded between{" "}
                {windowLabel(report.window_start, report.window_end)}. Nothing was
                summarised, because a report built from an empty period is where
                invention starts.
              </p>
              {report.notes.length > 0 && (
                <p className="muted report-quiet-scope">{report.notes.join(" ")}</p>
              )}
            </div>
          ) : (
            <AnswerText text={report.report_text} />
          )}
        </section>

        {/* Coverage stays before the evidence — what was NOT checked changes
            how the reader weighs the summary — but as a compact strip. As a
            full panel with its own heading it out-weighed the report itself,
            which for a one-line scope note is the wrong emphasis. */}
        {report.notes.length > 0 && report.item_count > 0 && (
          <p className="report-coverage">
            <span className="report-coverage-label">Checked</span>
            {report.notes.join(" ")}
          </p>
        )}

        {report.items.length > 0 && (
          <section className="studio-panel" aria-labelledby="activity-title">
            <div className="studio-section-head">
              <h2 id="activity-title">
                Activity this was built from
                <span className="report-count">{report.items.length}</span>
              </h2>
              <p className="muted">
                Every link goes to the source itself — the summary above never writes
                one, so none of these can be invented.
              </p>
            </div>
            <ul className="report-items">
              {(allItems ? report.items : report.items.slice(0, ITEMS_SHOWN)).map((item, i) => {
                const { who, where, when, text } = tidyItem(item);
                const body = (
                  <>
                    <span className="report-item-avatar" aria-hidden>
                      {(who || where.replace(/^#/, "") || "•").charAt(0).toUpperCase()}
                    </span>
                    <span className="report-item-meta">
                      {who && <b>{who}</b>}
                      {where && <span className="report-item-where">{where}</span>}
                      {when && <span className="report-item-when">{when}</span>}
                    </span>
                    <span className="report-item-text">{text}</span>
                  </>
                );
                return (
                  <li key={`${i}-${item.summary}`}>
                    {isWebUrl(item.url) ? (
                      <a href={item.url} target="_blank" rel="noreferrer noopener" title={text}>
                        {body}
                        <span className="report-item-go" aria-hidden>
                          ↗
                        </span>
                      </a>
                    ) : (
                      <div title={text}>{body}</div>
                    )}
                  </li>
                );
              })}
            </ul>
            {report.items.length > ITEMS_SHOWN && (
              <button className="report-items-more" type="button" onClick={() => setAllItems((v) => !v)}>
                {allItems ? "Show fewer" : `Show all ${report.items.length}`}
              </button>
            )}
          </section>
        )}

        <p className="muted" style={{ fontSize: "0.8rem" }}>
          {report.delivered
            ? "Emailed to you when it was generated."
            : "The notification email could not be delivered — this page is the report."}{" "}
          <Link href="/schedulers">Back to scheduled reports</Link>
        </p>
      </main>
    </AppShell>
  );
}
