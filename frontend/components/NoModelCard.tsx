"use client";

import Link from "next/link";

import type { Me } from "@/lib/api";

/** True when nothing can answer this person: no Handbook model, company key or own key. */
export function noModel(me: Me | null | undefined): boolean {
  return Boolean(me?.ai && !me.ai.ready);
}

/** True when the COMPANY has no model (Handbook's off, no company key): company
 *  work such as scheduled reports never runs on a person's own key. */
export function noCompanyModel(me: Me | null | undefined): boolean {
  return Boolean(me?.ai && !me.ai.handbook && !me.ai.company);
}

/**
 * Shown wherever AI work starts (chat, starters, Agent, charts, reports) while
 * no model is connected. The admin can fix it, so they get the button to
 * Admin › Model; anyone else learns their admins have a reminder, and can use
 * their own key meanwhile.
 */
export function NoModelCard({
  me,
  nudge = false,
  compact = false,
  companyWork = false,
}: {
  me: Me;
  nudge?: boolean;
  compact?: boolean;
  /** For company work (reports): a person's own key does not run it, so it is not offered. */
  companyWork?: boolean;
}) {
  const admin = me.role === "admin";
  return (
    <section
      className={`no-model-card${nudge ? " is-nudged" : ""}${compact ? " is-compact" : ""}`}
      role="status"
      aria-live="polite"
    >
      <span className="no-model-card-icon" aria-hidden>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
          <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </span>
      <div className="no-model-card-body">
        <strong>{admin ? "Connect your AI model to start" : "Your company hasn’t connected an AI model yet"}</strong>
        <p>
          {companyWork
            ? admin
              ? "Reports are written with your company’s AI model. Add its key once and everyone’s reports can run."
              : "Reports are written with your company’s AI model. Your admins have a reminder to connect one."
            : admin
              ? "Handbook answers with your company’s own AI model. Add its key once and everyone can ask, chart and draft."
              : "Your admins have a reminder to connect one. Meanwhile you can use your own key."}
        </p>
      </div>
      <div className="no-model-card-actions">
        {admin ? (
          <Link className="button button-sm" href="/admin/model">
            Connect model
          </Link>
        ) : null}
        {!companyWork && (
          <Link className={`button button-sm${admin ? " button-secondary" : ""}`} href="/account/model">
            {admin ? "Use my own key" : "Add my own key"}
          </Link>
        )}
      </div>
    </section>
  );
}
