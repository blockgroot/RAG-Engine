"use client";

/**
 * Admin-only: connect the organisation's own model.
 * Says which calls use the org's key (chat answers) and which still run on
 * ours (indexing, the quality check): implying full isolation would mislead.
 */

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { ApiError, api, type ModelPreset, type OrgModel } from "@/lib/api";

/** Agent mode's tool-calling test, said in the admin's terms. */
const AGENT_RESULT: Record<NonNullable<OrgModel["agent"]>, string> = {
  native: "Agent mode: works (calls tools)",
  text: "Agent mode: works with a fallback (writes tool calls as text)",
  none: "Agent mode: not available (this model doesn't call tools)",
  unknown: "Agent mode: couldn't be tested — save again to retry",
};

/** Provider logos (public/providers, from the MIT-licensed lobehub icon set).
 *  An id without a file falls back to its first letter. */
const LOGOS = new Set(["openai", "anthropic", "openrouter", "nvidia", "google", "mistral", "deepseek", "xai", "together", "fireworks", "cerebras", "perplexity", "cohere", "groq"]);

function ProviderLogo({ id, label }: { id?: string; label?: string }) {
  return (
    <i aria-hidden className="provider-logo">
      {id && LOGOS.has(id) ? <img src={`/providers/${id}.svg`} alt="" width={18} height={18} /> : (label ?? "?").charAt(0)}
    </i>
  );
}

function ProviderPicker({
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

export default function AdminModelPage() {
  const { me, loading } = useMe({ requireAdmin: true });

  const [presets, setPresets] = useState<ModelPreset[]>([]);
  const [saved, setSaved] = useState<OrgModel | null>(null);

  const [preset, setPreset] = useState("openai");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [{ presets: list }, { model: current }] = await Promise.all([
        api.listModelPresets(),
        api.getOrgModel(),
      ]);
      setPresets(list);
      setSaved(current);
      if (current?.preset) setPreset(current.preset);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load this page.");
    }
  }, []);

  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading&hellip;</p>
      </main>
    );
  }

  const activePreset = presets.find((p) => p.id === preset);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const { model: next } = await api.saveOrgModel(preset, model.trim(), apiKey.trim());
      setSaved(next);
      setModel("");
      setApiKey("");
      setNotice("Saved. Everyone in your company can now pick this model in chat.");
    } catch (err) {
      // The provider's own words: 401 (wrong key) vs 404 (wrong model id).
      setError(err instanceof ApiError ? err.message : "Could not save that.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.deleteOrgModel();
      setSaved(null);
      setNotice("Removed. Everyone is back to the built-in models.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove that.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack model-admin-page">
        <PageHeader
          eyebrow="Company"
          title="Company model"
          description="Connect one model for your team’s chat answers. This is optional; Handbook’s built-in models remain available."
          meta={
            <span className="hero-facts">
              {saved ? (
                <span className="hero-fact-ok">Connected to {saved.preset_label}</span>
              ) : (
                <>Using Handbook&rsquo;s built-in models</>
              )}
            </span>
          }
        />

        {error && (
          <div className="banner banner-warn" role="alert">
            {error}
          </div>
        )}
        {notice && (
          <div className="banner banner-ok" role="status">
            {notice}
          </div>
        )}

        {saved && (
          <section className="room-section model-current-panel" aria-labelledby="current-title">
            <div className="studio-section-head">
              <p className="model-section-label">Current connection</p>
              <h2 id="current-title">Available to your team</h2>
            </div>
            <div className="model-current">
              <div className="model-current-identity">
                <span className="model-current-name">{saved.model}</span>
                <span>{saved.preset_label} · key ending {saved.key_tail}</span>
              </div>
              <span className="model-current-checked">
                Last tested {saved.checked_at ? new Date(saved.checked_at).toLocaleDateString() : "—"}
              </span>
              {saved.agent && <span className="model-current-checked">{AGENT_RESULT[saved.agent]}</span>}
              <button className="button button-secondary" onClick={remove} disabled={busy}>
                Remove model
              </button>
            </div>
          </section>
        )}

        <section className="room-section model-setup" aria-labelledby="add-title">
          <div className="studio-section-head">
            <h2 id="add-title">{saved ? "Replace your model" : "Connect your model"}</h2>
            <p className="muted">We test it before it becomes available to your team.</p>
          </div>

          <form onSubmit={save} className="model-form">
            <div className="field">
              <label htmlFor="preset"><span aria-hidden>1</span> Provider</label>
              <ProviderPicker presets={presets} value={preset} onChange={setPreset} disabled={busy} />
            </div>

            <div className="field">
              <label htmlFor="model"><span aria-hidden>2</span> Model ID</label>
              <input
                id="model"
                className="input"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="For example, gpt-5"
                disabled={busy}
                required
              />
              <p className="muted">
                Use the provider&rsquo;s exact ID.{" "}
                {activePreset && (
                  <a href={activePreset.models_url} target="_blank" rel="noreferrer">
                    Browse model IDs
                  </a>
                )}
              </p>
            </div>

            <div className="field field-wide">
              <label htmlFor="apiKey"><span aria-hidden>3</span> API key</label>
              <input
                id="apiKey"
                className="input"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Paste the full API key"
                disabled={busy}
                required
                autoComplete="off"
              />
              <p className="muted">
                Encrypted before storage. Only the final four characters remain visible after saving.
              </p>
            </div>

            <div className="model-form-actions">
              <div className="model-test-note">
                <svg viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path d="m7 12 3 3 7-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.7" />
                </svg>
                <span>A short test runs first. Nothing is saved if it fails.</span>
              </div>
              <button
                className="button"
                type="submit"
                disabled={busy || !model.trim() || !apiKey.trim()}
              >
                {busy ? "Testing connection…" : "Test and connect"}
              </button>
            </div>
          </form>
        </section>

        <section className="room-section model-boundary" aria-labelledby="usage-title">
          <div className="studio-section-head">
            <h2 id="usage-title">How your model is used</h2>
            <p className="muted">Connecting a key changes one part of Handbook, not all of it.</p>
          </div>
          <div className="model-split">
            <article className="model-split-card is-yours">
              <header>
                <span className="model-split-icon" aria-hidden>
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                    <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5H11l-4.5 3.5V17A2.5 2.5 0 0 1 4 14.5v-8Z" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
                  </svg>
                </span>
                <div>
                  <span className="model-split-tag">Your key</span>
                  <h3>Answers in chat</h3>
                </div>
              </header>
              <ol className="model-split-flow">
                <li><span>Question</span></li>
                <li><span className="is-model">{activePreset?.label ?? "Your model"}</span></li>
                <li><span>Answer</span></li>
              </ol>
              <p>Every chat answer uses your model. Billed by {activePreset?.label ?? "your provider"}.</p>
            </article>
            <article className="model-split-card">
              <header>
                <span className="model-split-icon" aria-hidden>
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                    <path d="M6.5 3.5h7l4.5 4.5v10a2.5 2.5 0 0 1-2.5 2.5h-9A2.5 2.5 0 0 1 4 18V6a2.5 2.5 0 0 1 2.5-2.5Z" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
                    <path d="M13.5 3.5V8H18M8 13h7M8 16.5h5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
                  </svg>
                </span>
                <div>
                  <span className="model-split-tag">Handbook&rsquo;s model</span>
                  <h3>Indexing and checks</h3>
                </div>
              </header>
              <ol className="model-split-flow">
                <li><span>Your documents</span></li>
                <li><span className="is-model">Handbook</span></li>
                <li><span>Search index</span></li>
              </ol>
              <p>Reading, indexing and quality checks. Your key is never used here.</p>
            </article>
          </div>
          <footer className="model-split-note">
            <span aria-hidden>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M12 3.5 19 6v5.5c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6l7-2.5Z" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
                <path d="m9 12 2 2 4-4" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <p>
              <strong>Built-in fallback stays available.</strong> If your key stops working, members can switch
              models and keep working.
            </p>
          </footer>
        </section>

      </main>
    </AppShell>
  );
}
