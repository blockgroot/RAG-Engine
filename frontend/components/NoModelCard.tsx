"use client";

import Link from "next/link";

import type { Me } from "@/lib/api";
import { Icon, LOGO_STRIP } from "./ModelSetup";
import { ProviderLogo } from "./ProviderPicker";

/** True when nothing can answer this person: no Handbook model, company key or own key. */
export function noModel(me: Me | null | undefined): boolean {
  return Boolean(me?.ai && !me.ai.ready);
}

/**
 * Shown wherever AI work starts (chat, starters, Agent, charts, reports) while
 * no model is connected. The admin can fix it, so they get the button to
 * Admin › Model; anyone else learns their admins have a reminder, and can use
 * their own key meanwhile. Styled like the Model pages (ModelSetup).
 */
export function NoModelCard({
  me,
  nudge = false,
  compact = false,
}: {
  me: Me;
  nudge?: boolean;
  compact?: boolean;
}) {
  const admin = me.role === "admin";
  const title = admin ? "Connect your AI model to start" : "Your company hasn’t connected an AI model yet";
  const text = admin
    ? "Handbook answers with your company’s own AI model. Add its key once and everyone can ask, chart and draft."
    : "Your admins have a reminder to connect one. Meanwhile you can add your own key and use it everywhere.";
  const steps = admin
    ? ["Choose a provider", "Paste its API key", "Everyone can ask"]
    : ["Add your own key", "Pick it in the model menu", "Ask away"];

  const actions = (
    <div className="nm-actions">
      {admin && (
        <Link className="button button-sm" href="/admin/model">
          <Icon name="key" size={15} /> Connect model
        </Link>
      )}
      <Link className={`button button-sm${admin ? " button-secondary" : ""}`} href="/account/model">
        {admin ? "Use my own key" : <><Icon name="key" size={15} /> Add my own key</>}
      </Link>
    </div>
  );

  return (
    <section
      className={`nm-card${nudge ? " is-nudged" : ""}${compact ? " is-compact" : ""}`}
      role="status"
      aria-live="polite"
    >
      <div className="nm-main">
        {compact ? (
          <span className="mk-icon-tile">
            <Icon name="spark" />
          </span>
        ) : (
          <div className="mk-logo-strip nm-logos" aria-hidden>
            {LOGO_STRIP.map((id) => (
              <ProviderLogo key={id} id={id} />
            ))}
          </div>
        )}
        <div className="nm-body">
          <strong>{title}</strong>
          <p>{text}</p>
        </div>
        {actions}
      </div>
      {!compact && (
        <ol className="nm-steps">
          {steps.map((s, i) => (
            <li key={s}>
              <b>{i + 1}</b> {s}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
