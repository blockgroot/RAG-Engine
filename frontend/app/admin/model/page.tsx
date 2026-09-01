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
                <strong>{saved.model}</strong> via {saved.preset_label} &middot; key ending{" "}
                {saved.key_tail} &middot;{" "}
                {/* Past tense and dated on purpose: a snapshot from when it was
                    saved, not a live health check. A green "Connected" badge
                    would claim something nobody has verified since. */}
                last checked{" "}
                {saved.checked_at
                  ? new Date(saved.checked_at).toLocaleDateString()
                  : "—"}
              </p>
            </div>
            <button className="button" onClick={remove} disabled={busy}>
              Remove
            </button>
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

          <form onSubmit={save} className="invite-form">
            <div className="field">
              <label htmlFor="preset">Provider</label>
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
                Stored encrypted. You&rsquo;ll only ever see the last 4 characters again
                &mdash; to change it, paste the whole key.
              </p>
            </div>

            <button
              className="button"
              type="submit"
              disabled={busy || !model.trim() || !apiKey.trim()}
            >
              {busy ? "Checking…" : "Test and save"}
            </button>
          </form>
        </section>

        <section className="studio-panel" aria-labelledby="usage-title">
          <div className="studio-panel-glow" aria-hidden />
          <div className="studio-section-head">
            <h2 id="usage-title">What your key is used for</h2>
          </div>
          <ul className="muted">
            <li>
              <strong>Answering questions in chat</strong>, for any member who picks your
              model from the dropdown.
            </li>
            <li>
              <strong>Not</strong> indexing your documents, and <strong>not</strong> the
              internal answer-quality check &mdash; those keep running on our model.
            </li>
            <li>
              Every member of your company can select it, so{" "}
              <strong>usage is billed to you</strong>. Set a spend limit with your provider.
            </li>
            <li>
              If your key stops working, members can switch back to a built-in model
              themselves.
            </li>
          </ul>
        </section>
      </main>
    </AppShell>
  );
}
