"use client";

/**
 * Your own model: a guided page for a person to add their own provider, key,
 * model and (optional) limits. Only they can pick it in chat; they pay their
 * provider, so Handbook's and the company's limits never apply, only the ones
 * set here (0 = no limit, the default). Laid out like Admin › Model.
 */

import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { PageLoader } from "@/components/PageLoader";
import { AGENT_RESULT, ProviderPicker } from "@/components/ProviderPicker";
import { useMe } from "@/lib/useMe";
import { ApiError, api, type ModelPreset, type OwnLimits, type OwnModel } from "@/lib/api";

const NO_LIMITS: OwnLimits = { tokens_per_minute: 0, tokens_per_day: 0 };

const LIMIT_ROWS: { key: keyof OwnLimits; label: string }[] = [
  { key: "tokens_per_minute", label: "Tokens a minute" },
  { key: "tokens_per_day", label: "Tokens a day" },
];

function TestNote({ text }: { text: string }) {
  return (
    <div className="model-test-note">
      <svg viewBox="0 0 24 24" fill="none" aria-hidden>
        <path d="m7 12 3 3 7-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.7" />
      </svg>
      <span>{text}</span>
    </div>
  );
}

function LimitFields({
  limits,
  onChange,
  disabled,
  step,
}: {
  limits: OwnLimits;
  onChange: (next: OwnLimits) => void;
  disabled: boolean;
  step?: number;
}) {
  return (
    <>
      {step && (
        <div className="field field-wide">
          <label htmlFor="tokens_per_minute">
            <span aria-hidden>{step}</span> Limits (optional)
          </label>
          <p className="muted">
            Caps on your own key, so a long day can&rsquo;t run up your bill. Tokens are the pieces of text a model
            reads and writes; a typical question uses 10,000 to 40,000. Leave 0 for no limit.
          </p>
        </div>
      )}
      {LIMIT_ROWS.map((row) => (
        <div className="field" key={row.key}>
          <label htmlFor={row.key}>{row.label}</label>
          <input
            id={row.key}
            className="input"
            type="number"
            inputMode="numeric"
            min={0}
            step={1000}
            value={limits[row.key]}
            disabled={disabled}
            onChange={(e) => onChange({ ...limits, [row.key]: Math.max(0, Math.floor(Number(e.target.value) || 0)) })}
          />
          <p className="muted">{limits[row.key] ? `${limits[row.key].toLocaleString()} tokens` : "No limit"}</p>
        </div>
      ))}
    </>
  );
}

export default function OwnModelPage() {
  const { me, loading } = useMe();

  const [presets, setPresets] = useState<ModelPreset[]>([]);
  const [saved, setSaved] = useState<OwnModel | null>(null);

  const [preset, setPreset] = useState("anthropic");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [limits, setLimits] = useState<OwnLimits>(NO_LIMITS);
  const [savedLimits, setSavedLimits] = useState<OwnLimits>(NO_LIMITS);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { model: current, presets: list } = await api.getOwnModel();
      setPresets(list);
      setSaved(current);
      if (current) {
        setPreset(current.preset);
        const now = { tokens_per_minute: current.tokens_per_minute, tokens_per_day: current.tokens_per_day };
        setLimits(now);
        setSavedLimits(now);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load this page.");
    }
  }, []);

  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  if (loading || !me) return <PageLoader />;

  const activePreset = presets.find((p) => p.id === preset);

  async function run(action: () => Promise<string>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setNotice(await action());
    } catch (err) {
      // The provider's own words: a wrong key and a wrong model id need different fixes.
      setError(err instanceof ApiError ? err.message : "Could not save that.");
    } finally {
      setBusy(false);
    }
  }

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const { model: next } = await api.saveOwnModel(preset, model.trim(), apiKey.trim(), limits);
      setSaved(next);
      setSavedLimits(limits);
      setModel("");
      setApiKey("");
      return "Saved. Pick “Your own model” in the chat’s model menu.";
    });
  };

  const saveLimits = () =>
    run(async () => {
      await api.saveOwnLimits(limits);
      setSavedLimits(limits);
      return "Limits saved. They apply from the next question.";
    });

  const remove = () =>
    run(async () => {
      await api.deleteOwnModel();
      setSaved(null);
      setLimits(NO_LIMITS);
      setSavedLimits(NO_LIMITS);
      return "Removed. Your key is deleted.";
    });

  const limitsChanged =
    limits.tokens_per_minute !== savedLimits.tokens_per_minute || limits.tokens_per_day !== savedLimits.tokens_per_day;

  return (
    <AppShell me={me}>
      <main className="page-wide studio-page stack model-admin-page">
        <PageHeader
          eyebrow="You"
          title="Your own model"
          description="Add your own AI provider and key to answer with the model you choose. Only you can pick it, and you pay your provider directly."
          meta={
            <span className="hero-facts">
              {saved ? (
                <span className="hero-fact-ok">Connected to {saved.preset_label}</span>
              ) : (
                <>Using your company&rsquo;s models</>
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
              <h2 id="current-title">Available only to you</h2>
            </div>
            <div className="model-current">
              <div className="model-current-identity">
                <span className="model-current-name">{saved.model}</span>
                <span>
                  {saved.preset_label} · key ending {saved.key_tail}
                </span>
              </div>
              {saved.agent && <span className="model-current-checked">{AGENT_RESULT[saved.agent]}</span>}
              <button className="button button-secondary" onClick={remove} disabled={busy}>
                Remove model
              </button>
            </div>
          </section>
        )}

        <section className="room-section model-setup" aria-labelledby="add-title">
          <div className="studio-section-head">
            <h2 id="add-title">{saved ? "Replace your model" : "Add your own key"}</h2>
            <p className="muted">We test it before it appears in your model menu.</p>
          </div>

          <form onSubmit={save} className="model-form">
            <div className="field">
              <label htmlFor="preset">
                <span aria-hidden>1</span> Provider
              </label>
              <ProviderPicker presets={presets} value={preset} onChange={setPreset} disabled={busy} />
            </div>

            <div className="field">
              <label htmlFor="apiKey">
                <span aria-hidden>2</span> API key
              </label>
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
              <p className="muted">Encrypted before storage. Only the final four characters remain visible after saving.</p>
            </div>

            <div className="field field-wide">
              <label htmlFor="model">
                <span aria-hidden>3</span> Model ID
              </label>
              <input
                id="model"
                className="input"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="For example, claude-sonnet-5-5"
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

            {!saved && <LimitFields limits={limits} onChange={setLimits} disabled={busy} step={4} />}

            <div className="model-form-actions">
              <TestNote text="A short test runs first. Nothing is saved if it fails." />
              <button className="button" type="submit" disabled={busy || !model.trim() || !apiKey.trim()}>
                {busy ? "Testing connection…" : saved ? "Test and replace" : "Test and save"}
              </button>
            </div>
          </form>
        </section>

        {saved && (
          <section className="room-section model-setup" aria-labelledby="limits-title">
            <div className="studio-section-head">
              <h2 id="limits-title">Your limits</h2>
              <p className="muted">
                Optional caps on your own key, so a long day can&rsquo;t run up your bill. Tokens are the pieces of
                text a model reads and writes; a typical question uses 10,000 to 40,000. 0 means no limit.
              </p>
            </div>
            <div className="model-form">
              <LimitFields limits={limits} onChange={setLimits} disabled={busy} />
              <div className="model-form-actions">
                <TestNote text="Applies from the next question." />
                <button className="button" type="button" onClick={saveLimits} disabled={busy || !limitsChanged}>
                  {busy ? "Saving…" : "Save limits"}
                </button>
              </div>
            </div>
          </section>
        )}

        <section className="room-section model-boundary" aria-labelledby="usage-title">
          <div className="studio-section-head">
            <h2 id="usage-title">How your model is used</h2>
          </div>
          <footer className="model-split-note">
            <span aria-hidden>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M12 3.5 19 6v5.5c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6l7-2.5Z" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
                <path d="m9 12 2 2 4-4" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <p>
              <strong>Only your chat answers use it.</strong> When you pick it, the documents behind your answer are
              sent to {activePreset?.label ?? "your provider"} on your key. Searching, indexing and quality checks
              stay on Handbook&rsquo;s model, and Handbook&rsquo;s and your company&rsquo;s limits don&rsquo;t apply.
            </p>
          </footer>
        </section>
      </main>
    </AppShell>
  );
}
