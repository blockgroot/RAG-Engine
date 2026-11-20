"use client";

import { useEffect, useRef, useState } from "react";

/** `group` heads a section of the menu ("Your key", "Company key"): whose key answers. */
export type ModelOption = { value: string; label: string; hint?: string; group?: string };

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
  footer,
}: {
  value: string;
  options: ModelOption[];
  disabled?: boolean;
  title?: string;
  onChange: (value: string) => void;
  /** A link under the list, e.g. to add your own key. */
  footer?: { href: string; label: string };
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  // A key can list dozens of models; past a handful, the menu searches.
  const searchable = options.length > 8;
  const q = query.trim().toLowerCase();
  const shown = q ? options.filter((o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q)) : options;
  const root = useRef<HTMLSpanElement>(null);
  const current = options.find((o) => o.value === value) ?? options[0];

  // A long menu opens on the current choice, not at the top (it may sit in the last section).
  useEffect(() => {
    if (open) root.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [open]);

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
    const items = [...e.currentTarget.querySelectorAll<HTMLElement>("button, a")];
    const at = items.indexOf(document.activeElement as HTMLElement);
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
        onClick={() => {
          setOpen((v) => !v);
          setQuery("");
        }}
      >
        <Logo label={current?.label} />
        <span className="model-picker-name">{current?.label}</span>
        <span className="model-picker-chevron" aria-hidden />
      </button>
      {open && (
        <ul className="model-picker-menu" role="listbox" aria-label="Model" onKeyDown={move}>
          {!options.some((o) => o.group) && <li className="model-picker-head" aria-hidden>Answer with</li>}
          {searchable && (
            <li className="model-picker-search">
              <input
                className="input"
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={`Search ${options.length} models`}
                aria-label="Search models"
              />
            </li>
          )}
          {shown.map((o, i) => [
            o.group && o.group !== shown[i - 1]?.group && (
              <li key={`group:${o.group}`} className="model-picker-head" aria-hidden>
                {o.group}
              </li>
            ),
            <li key={o.value} role="option" aria-selected={o.value === value}>
              <button
                type="button"
                autoFocus={!searchable && (o.value === value || (i === 0 && !options.some((x) => x.value === value)))}
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
            </li>,
          ])}
          {footer && (
            <li className="model-picker-foot">
              <a href={footer.href}>{footer.label}</a>
            </li>
          )}
        </ul>
      )}
    </span>
  );
}
