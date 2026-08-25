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

/** "1–8 Sep" — the window the report actually covers, not when it was sent. */
function windowLabel(start: string, end: string): string {
  const from = new Date(start);
  const to = new Date(end);
  const opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  return `${from.toLocaleDateString([], opts)} – ${to.toLocaleDateString([], opts)}`;
}

function sentLabel(iso: string): string {
  const when = new Date(iso);
  return when.toLocaleDateString([], {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
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
      <main className="page studio-page stack">
        <PageHeader
          eyebrow={`${report.frequency} report`}
          title={report.title}
          description={`Covering ${windowLabel(report.window_start, report.window_end)} · sent ${sentLabel(report.created_at)}`}
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
          <AnswerText text={report.report_text} />
        </section>

        {/* Coverage before evidence, deliberately: what was NOT checked
            changes how the reader weighs everything above. */}
        {report.notes.length > 0 && (
          <section className="studio-panel" aria-labelledby="coverage-title">
            <div className="studio-section-head">
              <h2 id="coverage-title">What was checked</h2>
              <p className="muted">
                Stated every run, so a report can never imply coverage it did not have.
              </p>
            </div>
            <ul className="report-notes">
              {report.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </section>
        )}

        {report.items.length > 0 && (
          <section className="studio-panel" aria-labelledby="activity-title">
            <div className="studio-section-head">
              <h2 id="activity-title">Activity this was built from</h2>
              <p className="muted">
                Every link comes from the source itself — the summary above never
                writes one, so none of these can be invented.
              </p>
            </div>
            <ul className="report-items">
              {report.items.map((item, i) => (
                <li key={`${i}-${item.summary}`}>
                  {item.url ? (
                    <a href={item.url} target="_blank" rel="noreferrer noopener">
                      {item.summary}
                    </a>
                  ) : (
                    <span>{item.summary}</span>
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
