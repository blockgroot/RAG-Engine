"use client";

import { useState } from "react";

import type { ModelPreset } from "@/lib/api";

/** Provider logos (public/providers, from the MIT-licensed lobehub icon set).
 *  An id without a file falls back to its first letter. */
const LOGOS = new Set(["openai", "anthropic", "openrouter", "nvidia", "google", "mistral", "deepseek", "xai", "together", "fireworks", "cerebras", "perplexity", "cohere", "groq"]);

export function ProviderLogo({ id, label }: { id?: string; label?: string }) {
  return (
    <i aria-hidden className="provider-logo">
      {id && LOGOS.has(id) ? <img src={`/providers/${id}.svg`} alt="" width={18} height={18} /> : (label ?? "?").charAt(0)}
    </i>
  );
}

export function ProviderPicker({
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
  const [open, setOpen] = useState(false);
  const picked = presets.find((p) => p.id === value);
  return (
    <div
      className="provider-picker"
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false);
      }}
      onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
    >
      <button
        type="button"
        id="preset"
        className="input provider-picker-button"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
      >
        <ProviderLogo id={picked?.id} label={picked?.label} />
        <span>{picked?.label ?? "Choose a provider"}</span>
        <svg width="12" height="12" viewBox="0 0 10 10" aria-hidden>
          <path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <div className="provider-menu" role="listbox" aria-label="Provider">
          {presets.map((p) => (
            <button
              key={p.id}
              type="button"
              role="option"
              aria-selected={p.id === value}
              onClick={() => {
                onChange(p.id);
                setOpen(false);
              }}
            >
              <ProviderLogo id={p.id} label={p.label} />
              <span>{p.label}</span>
              {p.id === value && (
                <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden>
                  <path d="m6 12 4 4 8-8" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
