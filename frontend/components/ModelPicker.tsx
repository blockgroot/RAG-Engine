"use client";

import { useEffect, useRef, useState } from "react";

export type ModelOption = { value: string; label: string; hint?: string };

/** The maker's logo for a model name (public/providers); none for an unknown one. */
function logoFor(label = ""): string | null {
  const n = label.toLowerCase();
  if (n.includes("claude")) return "claude";
  if (n.includes("gemini")) return "google";
  if (n.includes("gpt") || /\bo\d/.test(n)) return "openai";
  if (n.includes("mistral")) return "mistral";
  if (n.includes("deepseek")) return "deepseek";
  if (n.includes("grok")) return "xai";
  return null;
}

function Logo({ label }: { label?: string }) {
  const id = logoFor(label);
  return id ? <img className="model-picker-logo" src={`/providers/${id}.svg`} alt="" width={14} height={14} /> : null;
}

/**
 * The composer's model choice. Our own menu, not a native <select>: macOS draws
 * that list grey and see-through over the composer, and the choice got lost.
 */
export function ModelPicker({
  value,
  options,
  disabled,
  title,
  onChange,
}: {
  value: string;
  options: ModelOption[];
  disabled?: boolean;
  title?: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const current = options.find((o) => o.value === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  function move(e: React.KeyboardEvent<HTMLUListElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = [...e.currentTarget.querySelectorAll<HTMLButtonElement>("button")];
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    items[(at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
  }

  return (
    <span className="model-picker" ref={root}>
      <button
        type="button"
        className="model-picker-button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Model: ${current?.label}`}
        title={title}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        <Logo label={current?.label} />
        <span className="model-picker-name">{current?.label}</span>
        <span className="model-picker-chevron" aria-hidden />
      </button>
      {open && (
        <ul className="model-picker-menu" role="listbox" aria-label="Model" onKeyDown={move}>
          <li className="model-picker-head" aria-hidden>Answer with</li>
          {options.map((o, i) => (
            <li key={o.value} role="option" aria-selected={o.value === value}>
              <button
                type="button"
                autoFocus={o.value === value || (i === 0 && !options.some((x) => x.value === value))}
                onClick={() => {
                  onChange(o.value);
                  setOpen(false);
                }}
              >
                <Logo label={o.label} />
                <span>
                  <strong>{o.label}</strong>
                  {o.hint && <small>{o.hint}</small>}
                </span>
                {o.value === value && <span className="model-picker-check" aria-hidden>✓</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </span>
  );
}
