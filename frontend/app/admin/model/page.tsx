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

/** Agent mode's tool-calling test, said in the admin's terms. */
const AGENT_RESULT: Record<NonNullable<OrgModel["agent"]>, string> = {
  native: "Agent mode: works (calls tools)",
  text: "Agent mode: works with a fallback (writes tool calls as text)",
  none: "Agent mode: not available (this model doesn't call tools)",
  unknown: "Agent mode: couldn't be tested — save again to retry",
};

/** The provider list as a menu: each one with its letter mark, the chosen one ticked. */
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
        <i aria-hidden>{(picked?.label ?? "?").charAt(0)}</i>
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
              <i aria-hidden>{p.label.charAt(0)}</i>
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
          <div className="model-lanes">
            <div className="model-lane is-yours">
              <span className="model-lane-end">A question in chat</span>
              <i aria-hidden />
              <span className="model-lane-node">
                <b>Your model</b>
                <small>Your key, billed by {activePreset?.label ?? "your provider"}</small>
              </span>
              <i aria-hidden />
              <span className="model-lane-end">The answer</span>
            </div>
            <div className="model-lane">
              <span className="model-lane-end">Your documents</span>
              <i aria-hidden />
              <span className="model-lane-node">
                <b>Handbook&rsquo;s model</b>
                <small>Your key is never used here</small>
              </span>
              <i aria-hidden />
              <span className="model-lane-end">Indexing and quality checks</span>
            </div>
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
