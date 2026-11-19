"use client";

/**
 * The pieces shared by Account › Your own model and Admin › Model, so the two
 * pages look and behave alike: the status card, the provider tiles, the three
 * setup steps and the limit tiles. Presentation only; each page keeps its own
 * state and API calls.
 */

import { useState, type ReactNode } from "react";

import type { ModelPreset } from "@/lib/api";
import { ProviderLogo } from "./ProviderPicker";

type IconName = "key" | "lock" | "clock" | "calendar" | "person" | "building" | "check" | "bolt" | "gauge" | "chat" | "doc" | "shield" | "spark" | "external";

const PATHS: Record<IconName, ReactNode> = {
  key: <><circle cx="8" cy="15" r="4" /><path d="m11 12 8-8M16 7l2 2M14 9l2 2" /></>,
  lock: <><rect x="5" y="10.5" width="14" height="9.5" rx="2.2" /><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  calendar: <><rect x="4" y="5.5" width="16" height="14.5" rx="2.5" /><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4" /></>,
  person: <><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5" /></>,
  building: <><path d="M5 20V5.5A1.5 1.5 0 0 1 6.5 4h7A1.5 1.5 0 0 1 15 5.5V20M15 9.5h3.5A1.5 1.5 0 0 1 20 11v9M3.5 20h17" /><path d="M8.5 8h3M8.5 11.5h3M8.5 15h3" /></>,
  check: <path d="m5.5 12.5 4 4 9-9" />,
  bolt: <path d="M13 3 5.5 13.5H12L11 21l7.5-10.5H12L13 3Z" />,
  gauge: <><path d="M4.5 16a7.5 7.5 0 1 1 15 0" /><path d="m12 16 3.5-4.5" /></>,
  chat: <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5H11l-4.5 3.5V17A2.5 2.5 0 0 1 4 14.5v-8Z" />,
  doc: <><path d="M6.5 3.5h7l4.5 4.5v10a2.5 2.5 0 0 1-2.5 2.5h-9A2.5 2.5 0 0 1 4 18V6a2.5 2.5 0 0 1 2.5-2.5Z" /><path d="M13.5 3.5V8H18M8 13h7M8 16.5h5" /></>,
  shield: <><path d="M12 3.5 19 6v5.5c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6l7-2.5Z" /><path d="m9 12 2 2 4-4" /></>,
  spark: <path d="M12 3.5 13.8 10.2 20.5 12l-6.7 1.8L12 20.5l-1.8-6.7L3.5 12l6.7-1.8L12 3.5Z" />,
  external: <path d="M14 4.5h5.5V10M19.5 4.5 11 13M17 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h4" />,
};

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {PATHS[name]}
    </svg>
  );
}

/** A white card with an icon tile, a title and an optional line under it. */
export function ModelCard({
  icon,
  title,
  description,
  aside,
  children,
}: {
  icon: IconName;
  title: string;
  description?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mk-card">
      <header className="mk-card-head">
        <span className="mk-icon-tile">
          <Icon name={icon} />
        </span>
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {aside}
      </header>
      {children}
    </section>
  );
}

const LOGO_STRIP = ["anthropic", "openai", "google", "mistral", "openrouter"];

/**
 * Top of the page: the connected model, or a quiet "not connected" state.
 * Removing takes a second click: it deletes the key and moves people back.
 */
export function ModelStatus({
  saved,
  scope,
  emptyTitle,
  emptyText,
  chips,
  busy,
  replacing,
  onReplace,
  onRemove,
}: {
  saved: { model: string; preset: string | null; preset_label: string | null; key_tail: string | null } | null;
  /** Who can pick it: "Only you", "Everyone at Acme". */
  scope: string;
  emptyTitle: string;
  emptyText: string;
  chips: { icon: IconName; text: string; tone?: "ok" | "warn" }[];
  busy: boolean;
  replacing: boolean;
  onReplace: () => void;
  onRemove: () => void;
}) {
  const [confirming, setConfirming] = useState(false);

  if (!saved) {
    return (
      <section className="mk-status is-empty">
        <div className="mk-logo-strip" aria-hidden>
          {LOGO_STRIP.map((id) => (
            <ProviderLogo key={id} id={id} />
          ))}
        </div>
        <div className="mk-status-body">
          <h2>{emptyTitle}</h2>
          <p>{emptyText}</p>
        </div>
      </section>
    );
  }

  return (
    <section className="mk-status">
      <div className="mk-status-logo">
        <ProviderLogo id={saved.preset ?? undefined} label={saved.preset_label ?? undefined} />
      </div>
      <div className="mk-status-body">
        <div className="mk-status-title">
          <h2>{saved.model}</h2>
          <span className="mk-pill is-ok">
            <i aria-hidden /> Active
          </span>
        </div>
        <p>
          {saved.preset_label ?? "Your provider"} · {scope}
        </p>
        <ul className="mk-chips">
          {saved.key_tail && (
            <li>
              <Icon name="key" size={14} /> Key ending {saved.key_tail}
            </li>
          )}
          {chips.map((c) => (
            <li key={c.text} className={c.tone ? `is-${c.tone}` : undefined}>
              <Icon name={c.icon} size={14} /> {c.text}
            </li>
          ))}
        </ul>
      </div>
      <div className="mk-status-actions">
        {confirming ? (
          <div className="mk-confirm" role="alert">
            <span>Remove this model and delete its key?</span>
            <div>
              <button className="button button-secondary button-sm" onClick={() => setConfirming(false)} disabled={busy}>
                Keep it
              </button>
              <button className="button button-danger button-sm" onClick={onRemove} disabled={busy}>
                {busy ? "Removing…" : "Remove"}
              </button>
            </div>
          </div>
        ) : (
          <>
            <button className="button button-secondary button-sm" onClick={onReplace} disabled={busy || replacing}>
              Replace
            </button>
            <button className="button button-ghost button-sm" onClick={() => setConfirming(true)} disabled={busy}>
              Remove
            </button>
          </>
        )}
      </div>
    </section>
  );
}

/** The providers as logo tiles: every choice visible at once. */
export function ProviderGrid({
  presets,
  value,
  onChange,
  disabled,
}: {
  presets: ModelPreset[];
  value: string;
  onChange: (id: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="mk-providers" role="radiogroup" aria-label="Provider">
      {presets.map((p) => (
        <button
          key={p.id}
          type="button"
          role="radio"
          aria-checked={p.id === value}
          className="mk-provider"
          onClick={() => onChange(p.id)}
          disabled={disabled}
        >
          <ProviderLogo id={p.id} label={p.label} />
          <span>{p.label}</span>
          {p.id === value && (
            <b className="mk-provider-check" aria-hidden>
              <Icon name="check" size={12} />
            </b>
          )}
        </button>
      ))}
    </div>
  );
}

/** One numbered step of the setup form. */
export function Step({ n, title, hint, children }: { n: number; title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="mk-step">
      <span className="mk-step-n" aria-hidden>
        {n}
      </span>
      <div className="mk-step-body">
        <h3>{title}</h3>
        {children}
        {hint && <p className="mk-hint">{hint}</p>}
      </div>
    </div>
  );
}

/** An input with an icon inside it on the left. */
export function IconInput({ icon, ...props }: { icon: IconName } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div className="mk-input">
      <Icon name={icon} size={16} />
      <input className="input" {...props} />
    </div>
  );
}

/** A token limit as a tile: what it caps, the number, and what that means. */
export function LimitTile({
  id,
  icon,
  label,
  value,
  onChange,
  disabled,
  note,
}: {
  id: string;
  icon: IconName;
  label: string;
  value: number;
  onChange: (next: number) => void;
  disabled: boolean;
  note?: string;
}) {
  return (
    <div className="mk-limit">
      <label htmlFor={id}>
        <span className="mk-icon-tile is-small">
          <Icon name={icon} size={15} />
        </span>
        {label}
      </label>
      <div className="mk-limit-field">
        <input
          id={id}
          className="input"
          type="text"
          inputMode="numeric"
          value={value ? value.toLocaleString() : "0"}
          disabled={disabled}
          onChange={(e) => onChange(Number(e.target.value.replace(/\D/g, "")) || 0)}
        />
        <span>tokens</span>
      </div>
      <p className={value ? "mk-hint" : "mk-hint is-none"}>{value ? note ?? "Capped" : "No limit"}</p>
    </div>
  );
}

/** The bar under a form: a reassuring note on the left, the buttons on the right. */
export function FormFooter({ note, children }: { note: string; children: ReactNode }) {
  return (
    <div className="mk-footer">
      <span className="mk-note">
        <Icon name="shield" size={16} /> {note}
      </span>
      <div className="mk-footer-actions">{children}</div>
    </div>
  );
}

/** A small "what runs where" tile. */
export function UseTile({ icon, tag, title, children, tone }: { icon: IconName; tag: string; title: string; children: ReactNode; tone?: "muted" }) {
  return (
    <article className={tone ? `mk-use is-${tone}` : "mk-use"}>
      <span className="mk-icon-tile">
        <Icon name={icon} />
      </span>
      <div>
        <span className="mk-use-tag">{tag}</span>
        <h3>{title}</h3>
        <p>{children}</p>
      </div>
    </article>
  );
}
