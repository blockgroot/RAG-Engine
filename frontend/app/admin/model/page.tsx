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
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Company"
          title="Model"
          description="Bring your own API key so your company's chat answers run on a model you choose and pay for. Optional — the built-in models keep working either way."
          scene="model"
          meta={
            saved ? (
              <span className="studio-chip studio-chip-ok">{saved.preset_label} connected</span>
            ) : (
              <span className="studio-chip">No model yet</span>
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
          <section className="studio-panel" aria-labelledby="current-title">
            <div className="studio-panel-glow" aria-hidden />
            <div className="studio-section-head">
              <h2 id="current-title">In use now</h2>
              <p className="muted">
                Everyone in your company can pick this in the chat model dropdown.
              </p>
            </div>
            <div className="model-current">
              <span className="model-current-name">{saved.model}</span>
              <span className="muted">via {saved.preset_label}</span>
              <span className="muted">&middot; key ending {saved.key_tail}</span>
              <span className="muted">
                {/* Past tense and dated on purpose: a snapshot from when it was
                    saved, not a live health check. A green "Connected" badge
                    would claim something nobody has verified since. */}
                &middot; last checked{" "}
                {saved.checked_at ? new Date(saved.checked_at).toLocaleDateString() : "—"}
              </span>
              <button className="button" onClick={remove} disabled={busy}>
                Remove
              </button>
            </div>
          </section>
        )}

        <section className="studio-panel" aria-labelledby="add-title">
          <div className="studio-panel-glow" aria-hidden />
          <div className="studio-section-head">
            <h2 id="add-title">{saved ? "Replace it" : "Add your model"}</h2>
            <p className="muted">
              We send one short test message before saving, so a typo never becomes a
              broken option for your team.
            </p>
          </div>

          <div className="model-split">
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
                <label htmlFor="model">Model name</label>
                <input
                  id="model"
                  className="input"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="gpt-5"
                  disabled={busy}
                  required
                />
                <p className="muted">
                  Must match the provider&rsquo;s exact id.{" "}
                  {activePreset && (
                    <a href={activePreset.models_url} target="_blank" rel="noreferrer">
                      See {activePreset.label}&rsquo;s model list
                    </a>
                  )}
                </p>
              </div>

              <div className="field">
                <label htmlFor="apiKey">API key</label>
                <input
                  id="apiKey"
                  className="input"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="sk-…"
                  disabled={busy}
                  required
                  autoComplete="off"
                />
                <p className="muted">
                  Stored encrypted. You&rsquo;ll only ever see the last 4 characters
                  again &mdash; to change it, paste the whole key.
                </p>
              </div>

              <div>
                <button
                  className="button"
                  type="submit"
                  disabled={busy || !model.trim() || !apiKey.trim()}
                >
                  {busy ? "Checking…" : "Test and save"}
                </button>
              </div>
            </form>

            {/* Fills what was dead space with the one thing an admin cannot
                otherwise check before saving: the entry their team will see. */}
            <aside className="model-preview">
              <span className="model-preview-label">Your team will see</span>
              <div className="model-preview-chip">
                <span className="model-preview-dot" aria-hidden />
                <span>
                  {model.trim()
                    ? `Your company's model — ${model.trim()}`
                    : saved
                      ? `Your company's model — ${saved.model}`
                      : "Your company's model — …"}
                </span>
              </div>
              <p className="muted">
                {model.trim() || saved
                  ? "In the model dropdown, next to the built-in models."
                  : "Type a model name to see how it will appear."}
              </p>
            </aside>
          </div>
        </section>

        <section className="studio-panel" aria-labelledby="usage-title">
          <div className="studio-panel-glow" aria-hidden />
          <div className="studio-section-head">
            <h2 id="usage-title">What your key is used for</h2>
            <p className="muted">Worth knowing before you paste a credential.</p>
          </div>
          <ul className="model-facts">
            <li className="model-fact">
              <span className="model-fact-mark model-fact-yes" aria-hidden>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                  <path d="M5 13l4 4L19 7" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
              <div>
                <strong>Answering questions in chat</strong>
                <p>For any member who picks your model from the dropdown.</p>
              </div>
            </li>
            <li className="model-fact">
              <span className="model-fact-mark model-fact-no" aria-hidden>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                  <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
                </svg>
              </span>
              <div>
                <strong>Not indexing your documents</strong>
                <p>Preparing your content and the internal answer-quality check keep running on our model.</p>
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
                <strong>Billed to you</strong>
                <p>Every member can select it, on every question. Set a spend limit with your provider.</p>
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
                <strong>Never a dead end</strong>
                <p>If your key stops working, members switch back to a built-in model themselves.</p>
              </div>
            </li>
          </ul>
        </section>

      </main>
    </AppShell>
  );
}
