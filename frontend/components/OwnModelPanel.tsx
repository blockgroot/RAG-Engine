"use client";

import { useEffect, useState } from "react";

import { AGENT_RESULT, ProviderPicker } from "@/components/ProviderPicker";
import { ApiError, api, type ModelPreset, type OwnModel } from "@/lib/api";

/**
 * Your own model: your key and model, picked in chat as "Your own model". You
 * pay your provider directly, so Handbook's and your company's limits never
 * apply to it. The key is tested once, then stored encrypted and never shown.
 */
export function OwnModelPanel() {
  const [presets, setPresets] = useState<ModelPreset[]>([]);
  const [saved, setSaved] = useState<OwnModel | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [preset, setPreset] = useState("anthropic");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api
      .getOwnModel()
      .then(({ model: current, presets: list }) => {
        setPresets(list);
        setSaved(current);
        if (current?.preset) setPreset(current.preset);
      })
      .catch((err) => setMessage({ ok: false, text: err instanceof ApiError ? err.message : "Could not load your model." }))
      .finally(() => setLoaded(true));
  }, []);

  const activePreset = presets.find((p) => p.id === preset);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const { model: next } = await api.saveOwnModel(preset, model.trim(), apiKey.trim());
      setSaved(next);
      setModel("");
      setApiKey("");
      setMessage({ ok: true, text: "Saved. Pick “Your own model” in the chat’s model menu." });
    } catch (err) {
      // The provider's own words: a wrong key and a wrong model id need different fixes.
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : "Could not save that." });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setMessage(null);
    try {
      await api.deleteOwnModel();
      setSaved(null);
      setMessage({ ok: true, text: "Removed. Your key is deleted." });
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : "Could not remove that." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section id="own-model" className="studio-panel stack" aria-labelledby="own-model-title">
      <div className="studio-section-head">
        <h2 id="own-model-title">Your own model</h2>
        <p className="muted">
          Use your own key and model in chat. You pay your provider directly, so no Handbook limits apply.
          Your company&rsquo;s documents are sent to that provider when you use it.
        </p>
      </div>

      {message && (
        <div className={`banner ${message.ok ? "banner-ok" : "banner-warn"}`} role={message.ok ? "status" : "alert"}>
          {message.text}
        </div>
      )}

      {saved && (
        <div className="model-current">
          <div className="model-current-identity">
            <span className="model-current-name">{saved.model}</span>
            <span>
              {saved.preset_label} · key ending {saved.key_tail}
            </span>
          </div>
          {saved.agent && <span className="model-current-checked">{AGENT_RESULT[saved.agent]}</span>}
          <button className="button button-secondary" onClick={remove} disabled={busy}>
            Remove
          </button>
        </div>
      )}

      {!loaded ? (
        <p className="muted">Loading…</p>
      ) : (
        <form onSubmit={save} className="model-form">
          <div className="field">
            <label htmlFor="preset">Provider</label>
            <ProviderPicker presets={presets} value={preset} onChange={setPreset} disabled={busy} />
          </div>
          <div className="field">
            <label htmlFor="own-model-id">Model ID</label>
            <input
              id="own-model-id"
              className="input"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="For example, claude-sonnet-5-5"
              disabled={busy}
              required
            />
            {activePreset && (
              <p className="muted">
                <a href={activePreset.models_url} target="_blank" rel="noreferrer">
                  Browse model IDs
                </a>
              </p>
            )}
          </div>
          <div className="field field-wide">
            <label htmlFor="own-api-key">API key</label>
            <input
              id="own-api-key"
              className="input"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Paste the full API key"
              disabled={busy}
              required
              autoComplete="off"
            />
            <p className="muted">Encrypted before it is stored. Only the last four characters are shown after.</p>
          </div>
          <div className="model-form-actions">
            <span className="muted">A short test runs first. Nothing is saved if it fails.</span>
            <button className="button" type="submit" disabled={busy || !model.trim() || !apiKey.trim()}>
              {busy ? "Testing…" : saved ? "Test and replace" : "Test and save"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
