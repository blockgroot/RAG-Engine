"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { AnswerText } from "@/components/AnswerText";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { api, ReportDetail } from "@/lib/api";

const PROVIDER_LABEL: Record<string, string> = {
  github: "GitHub",
  slack: "Slack",
  linear: "Linear",
};

/**
 * "Mon 18 – Mon 25 Aug" — the window the report covers, not when it was sent.
 * Weekday included because "which week was this?" is the question a reader
 * actually has, and a bare date makes them count.
 */
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

export default function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me, loading } = useMe();
  const [report, setReport] = useState<ReportDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow={`${report.frequency} report`}
          title={report.title}
          description={`${report.frequency === "weekly" ? "Week of" : "Period"} ${windowLabel(
            report.window_start,
            report.window_end
          )} · generated ${sentLabel(report.created_at)}`}
          scene="reports"
          meta={
            <>
              <span className="studio-chip">{provider}</span>
              <span className="studio-chip">{report.space_name ?? "Company-wide"}</span>
              <span className="studio-chip">
                {report.frequency === "weekly" ? "Weekly" : "Monthly"}
              </span>
              {report.item_count > 0 ? (
                <span className="studio-chip">
                  {report.item_count} item{report.item_count === 1 ? "" : "s"}
                </span>
              ) : (
                <span className="studio-chip">Quiet period</span>
              )}
            </>
          }
        />

        <section className="studio-panel report-body" aria-label="Report">
          <div className="studio-panel-glow" aria-hidden />
          {report.item_count === 0 ? (
            // A quiet period is a RESULT, not an error, and the fixed note on
            // its own read like something had gone wrong. Saying what was
            // looked at and over which dates is what makes it trustworthy —
            // and no model is asked to write any of it.
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
              {report.items.map((item, i) => (
                <li key={`${i}-${item.summary}`}>
                  {/* Clamped to two lines: one long Slack post used to fill a
                      screen and bury every item after it. title= keeps the
                      whole thing reachable without an expand control. */}
                  {item.url ? (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      title={item.summary}
                    >
                      {item.summary}
                    </a>
                  ) : (
                    <span title={item.summary}>{item.summary}</span>
                  )}
                </li>
              ))}
            </ul>
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
