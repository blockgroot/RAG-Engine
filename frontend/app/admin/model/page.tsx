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

        <section className="studio-panel model-setup-panel" aria-labelledby="add-title">
          <header className="model-setup-head">
            <div>
              <p className="model-section-label">{saved ? "Change connection" : "Model setup"}</p>
              <h2 id="add-title">{saved ? "Replace your model" : "Connect your model"}</h2>
              <p>We verify the credentials before this model becomes available to your team.</p>
            </div>
            <span>3-step setup</span>
          </header>

          <form onSubmit={save} className="model-form">
            <div className="field">
              <label htmlFor="preset"><span aria-hidden>1</span> Provider</label>
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

        <section className="model-boundary" aria-labelledby="usage-title">
          <header>
            <p className="model-section-label">Data route</p>
            <h2 id="usage-title">How your model is used</h2>
            <p>Connecting a key changes one part of Handbook—not the whole system.</p>
          </header>
          <div className="model-boundary-routes">
            <article>
              <span className="model-route-icon" aria-hidden>
                <svg viewBox="0 0 24 24" fill="none">
                  <path d="M5 6h14v10H9l-4 3V6Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
                </svg>
              </span>
              <div>
                <span>Chat answers</span>
                <strong>Your selected model</strong>
                <p>Uses your API key and is billed by your provider.</p>
              </div>
            </article>
            <article>
              <span className="model-route-icon model-route-icon-muted" aria-hidden>
                <svg viewBox="0 0 24 24" fill="none">
                  <path d="M6 4h9l3 3v13H6V4Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
                  <path d="M9 11h6M9 15h6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                </svg>
              </span>
              <div>
                <span>Indexing and quality checks</span>
                <strong>Handbook’s model</strong>
                <p>Your key is never used for document processing.</p>
              </div>
            </article>
          </div>
          <footer>
            <strong>Built-in fallback stays available.</strong>
            <span>If your key stops working, members can switch models and keep working.</span>
          </footer>
        </section>

      </main>
    </AppShell>
  );
}
