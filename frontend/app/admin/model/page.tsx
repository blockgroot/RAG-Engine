"use client";

import { PageLoader } from "@/components/PageLoader";
/**
 * Admin-only: connect the organisation's own model.
 * Says which calls use the org's key (chat answers) and which still run on
 * ours (indexing, the quality check): implying full isolation would mislead.
 */

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { ApiError, api, type ModelPreset, type OrgLimits, type OrgModel } from "@/lib/api";

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

const NO_LIMITS: OrgLimits = {
  person_tokens_per_minute: 0,
  person_tokens_per_day: 0,
  company_tokens_per_minute: 0,
  company_tokens_per_day: 0,
};

const LIMIT_INPUTS: { key: keyof OrgLimits; label: string }[] = [
  { key: "person_tokens_per_minute", label: "Each person, per minute" },
  { key: "person_tokens_per_day", label: "Each person, per day" },
  { key: "company_tokens_per_minute", label: "Whole company, per minute" },
  { key: "company_tokens_per_day", label: "Whole company, per day" },
];

/** The key's models as chips; a long list folds after the first few. */
function ModelChips({ models }: { models: string[] }) {
  const [all, setAll] = useState(false);
  const FOLD = 8;
  return (
    <ul className="model-chips" aria-label="Models in the chat menu">
      {(all ? models : models.slice(0, FOLD)).map((m) => (
        <li key={m}>{m}</li>
      ))}
      {models.length > FOLD && (
        <li>
          <button type="button" onClick={() => setAll((v) => !v)}>
            {all ? "Show fewer" : `+${models.length - FOLD} more`}
          </button>
        </li>
      )}
    </ul>
  );
}

export default function AdminModelPage() {
  const { me, loading } = useMe({ requireAdmin: true });

  const [presets, setPresets] = useState<ModelPreset[]>([]);
  const [saved, setSaved] = useState<OrgModel | null>(null);

  const [preset, setPreset] = useState("openai");
  const [apiKey, setApiKey] = useState("");
  // Set only when the provider publishes no model list: then the admin types IDs.
  const [typeIds, setTypeIds] = useState(false);
  const [typed, setTyped] = useState("");

  const [limits, setLimits] = useState<OrgLimits>(NO_LIMITS);
  const [savedLimits, setSavedLimits] = useState<OrgLimits>(NO_LIMITS);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [{ presets: list }, { model: current }, { limits: now }] = await Promise.all([
        api.listModelPresets(),
        api.getOrgModel(),
        api.getOrgLimits(),
      ]);
      setPresets(list);
      setLimits(now);
      setSavedLimits(now);
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
      <PageLoader />
    );
  }

  const activePreset = presets.find((p) => p.id === preset);

  // A blank key reuses the saved one, so the models can change without pasting it again.
  const reusingKey = !apiKey.trim() && saved?.preset === preset;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const typedIds = typeIds ? typed.split(/[\s,]+/).filter(Boolean) : [];
      const res = await api.saveOrgModel(preset, apiKey.trim(), typedIds);
      if (!res.model) {
        setTypeIds(true);
        setNotice(res.type_ids ?? "This provider doesn’t list its models. Type the model IDs.");
        return;
      }
      const next = res.model;
      setSaved(next);
      setApiKey("");
      setTyped("");
      setTypeIds(false);
      setNotice(`Saved. Everyone in your company can now pick from ${next.models.length} ${next.models.length === 1 ? "model" : "models"} in chat.`);
    } catch (err) {
      // The provider's own words: 401 (wrong key) vs 404 (wrong model id).
      setError(err instanceof ApiError ? err.message : "Could not save that.");
    } finally {
      setBusy(false);
    }
  }

  async function saveLimits(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const { limits: next } = await api.saveOrgLimits(limits);
      setLimits(next);
      setSavedLimits(next);
      setNotice("Limits saved. They apply from the next question.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the limits.");
    } finally {
      setBusy(false);
    }
  }

  const limitsChanged = LIMIT_INPUTS.some(({ key }) => limits[key] !== savedLimits[key]);

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
          description="Connect your company’s AI key; every chat model it offers appears in your team’s model menu. This is optional; Handbook’s built-in model remains available."
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
                <strong className="model-current-provider">{saved.preset_label}</strong>
                <span>
                  {saved.models.length} {saved.models.length === 1 ? "model" : "models"} in the chat menu · key ending {saved.key_tail}
                </span>
              </div>
              <span className="model-current-checked">
                Last tested {saved.checked_at ? new Date(saved.checked_at).toLocaleDateString() : "—"}
              </span>
              <button className="button button-secondary" onClick={remove} disabled={busy}>
                Remove model
              </button>
              <ModelChips models={saved.models} />
            </div>
          </section>
        )}

        {saved && (
          <section className="room-section model-setup" aria-labelledby="limits-title">
            <div className="studio-section-head">
              <p className="model-section-label">Usage limits</p>
              <h2 id="limits-title">Limits on the company key</h2>
              <p className="muted">
                Caps on answers from the company model, so a busy day can&rsquo;t run up your bill. A typical question uses
                10,000 to 40,000 tokens. Leave 0 for no limit. Handbook&rsquo;s built-in model keeps its own limits.
              </p>
            </div>
            <form onSubmit={saveLimits} className="model-form">
              {LIMIT_INPUTS.map(({ key, label }) => (
                <div className="field" key={key}>
                  <label htmlFor={key}>{label}</label>
                  <input
                    id={key}
                    className="input"
                    type="number"
                    min={0}
                    step={1000}
                    inputMode="numeric"
                    value={limits[key]}
                    onChange={(e) => setLimits({ ...limits, [key]: Math.max(0, Math.floor(Number(e.target.value) || 0)) })}
                    disabled={busy}
                  />
                  <p className="muted">{limits[key] ? `${limits[key].toLocaleString()} tokens` : "No limit"}</p>
                </div>
              ))}
              <div className="model-form-actions">
                <div className="model-test-note">
                  <span>Each person sees what is left of today&rsquo;s limit as a ring in the chat&rsquo;s model button.</span>
                </div>
                <button className="button" type="submit" disabled={busy || !limitsChanged}>
                  {busy ? "Saving…" : "Save limits"}
                </button>
              </div>
            </form>
          </section>
        )}

        <section className="room-section model-setup" aria-labelledby="add-title">
          <div className="studio-section-head">
            <h2 id="add-title">{saved ? "Replace your model" : "Connect your model"}</h2>
            <p className="muted">We check the key with the provider before your team sees anything.</p>
          </div>

          <form onSubmit={save} className="model-form">
            <div className="field">
              <label htmlFor="preset"><span aria-hidden>1</span> Provider</label>
              <ProviderPicker
                presets={presets}
                value={preset}
                onChange={(p) => {
                  setPreset(p);
                  setTypeIds(false);
                }}
                disabled={busy}
              />
            </div>

            <div className="field">
              <label htmlFor="apiKey"><span aria-hidden>2</span> API key</label>
              <input
                id="apiKey"
                className="input"
                type="password"
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                }}
                placeholder={saved?.preset === preset ? `Leave blank to keep the key ending ${saved.key_tail}` : "Paste the full API key"}
                disabled={busy}
                autoComplete="off"
              />
              <p className="muted">
                Encrypted before storage. Only the final four characters remain visible after saving.
              </p>
            </div>

            {typeIds && (
              <div className="field field-wide">
                <label htmlFor="typedIds"><span aria-hidden>3</span> Model IDs</label>
                <input
                  id="typedIds"
                  className="input"
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                  placeholder="Model IDs, separated by commas"
                  disabled={busy}
                />
                <p className="muted">
                  This provider doesn&rsquo;t list its models, so each ID gets one short test.{" "}
                  {activePreset && (
                    <a href={activePreset.models_url} target="_blank" rel="noreferrer">
                      Browse model IDs
                    </a>
                  )}
                </p>
              </div>
            )}

            <div className="model-form-actions">
              <div className="model-test-note">
                <svg viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path d="m7 12 3 3 7-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.7" />
                </svg>
                <span>We read the key&rsquo;s model list from the provider; every chat model on it appears in the chat&rsquo;s model menu.</span>
              </div>
              <button className="button" type="submit" disabled={busy || (!apiKey.trim() && !reusingKey) || (typeIds && !typed.trim())}>
                {busy ? "Reading the key’s models…" : saved && reusingKey ? "Refresh models" : "Connect"}
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
