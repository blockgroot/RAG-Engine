"use client";

/**
 * Admin-only: connect the organisation's own model.
 *
 * Deliberately says out loud which calls use the org's key and which do not.
 * Chat answers route to their model; indexing documents and the internal
 * answer-quality check still run on ours. An admin pasting a credential on a
 * page that implied full isolation would form a false belief about where their
 * company's content goes, and that is a compliance question, not a UI nicety.
 */

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { ApiError, api, type ModelPreset, type OrgModel } from "@/lib/api";

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
      // The provider's own words, not a generic failure. Telling 401 (wrong
      // key) from 404 (wrong model id) is the difference between a two-minute
      // fix and giving up — and a wrong model id is the predicted failure here.
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
            saved ? (
              <span className="studio-chip studio-chip-ok">Connected to {saved.preset_label}</span>
            ) : (
              <span className="studio-chip">Using built-in models</span>
            )
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
          <section className="studio-panel model-current-panel" aria-labelledby="current-title">
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
              <button className="button button-secondary" onClick={remove} disabled={busy}>
                Remove model
              </button>
            </div>
          </section>
        )}

        <div className="model-settings-layout">
          <section className="studio-panel model-setup-panel" aria-labelledby="add-title">
            <div className="studio-section-head">
              <p className="model-section-label">{saved ? "Change connection" : "Setup"}</p>
              <h2 id="add-title">{saved ? "Replace your model" : "Connect your model"}</h2>
              <p className="muted">
                We test the provider, model name and key before saving anything.
              </p>
            </div>

            <form onSubmit={save} className="model-form">
              <div className="field">
                <label htmlFor="preset">Provider</label>
                <span className="model-select-wrap">
                  <select
                    id="preset"
                    className="input"
                    value={preset}
                    onChange={(e) => setPreset(e.target.value)}
                    disabled={busy}
                  >
                    {presets.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                </span>
              </div>

              <div className="field">
                <label htmlFor="model">Model ID</label>
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
                  Enter the provider&rsquo;s exact model ID.{" "}
                  {activePreset && (
                    <a href={activePreset.models_url} target="_blank" rel="noreferrer">
                      View model IDs
                    </a>
                  )}
                </p>
              </div>

              <div className="field field-wide">
                <label htmlFor="apiKey">API key</label>
                <input
                  id="apiKey"
                  className="input"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Paste the full key"
                  disabled={busy}
                  required
                  autoComplete="off"
                />
                <p className="muted">
                  Encrypted before storage. After saving, only the final four characters remain visible.
                </p>
              </div>

              <div className="model-form-actions">
                <button
                  className="button"
                  type="submit"
                  disabled={busy || !model.trim() || !apiKey.trim()}
                >
                  {busy ? "Testing connection…" : "Test and connect"}
                </button>
                <span className="muted">Nothing is saved if the test fails.</span>
              </div>
            </form>
          </section>

          <aside className="studio-panel model-usage-panel" aria-labelledby="usage-title">
            <div className="studio-section-head">
              <p className="model-section-label">Good to know</p>
              <h2 id="usage-title">Before you connect</h2>
              <p className="muted">Your key changes chat answers only.</p>
            </div>
            <ul className="model-facts">
              <li className="model-fact">
                <span className="model-fact-mark model-fact-yes" aria-hidden>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                    <path d="M5 13l4 4L19 7" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
                <div>
                  <strong>Used for chat answers</strong>
                  <p>Members can select this model for any question.</p>
                </div>
              </li>
              <li className="model-fact">
                <span className="model-fact-mark model-fact-no" aria-hidden>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                    <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
                  </svg>
                </span>
                <div>
                  <strong>Not used for indexing</strong>
                  <p>Document processing and quality checks continue on Handbook’s model.</p>
                </div>
              </li>
              <li className="model-fact">
                <span className="model-fact-mark model-fact-bill" aria-hidden>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                    <path d="M12 7v10M9.5 9.5a2.5 2.5 0 012.5-2 2.2 2.2 0 012.4 1.7M14.5 14.5a2.5 2.5 0 01-2.5 2 2.2 2.2 0 01-2.4-1.7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                    <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" />
                  </svg>
                </span>
                <div>
                  <strong>Billed by your provider</strong>
                  <p>Set usage or spend limits in the provider’s account.</p>
                </div>
              </li>
              <li className="model-fact">
                <span className="model-fact-mark model-fact-back" aria-hidden>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                    <path d="M4 12a8 8 0 108-8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                    <path d="M4 5v5h5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
                <div>
                  <strong>Built-in fallback remains</strong>
                  <p>Members can switch back if your key becomes unavailable.</p>
                </div>
              </li>
            </ul>
          </aside>
        </div>

      </main>
    </AppShell>
  );
}
