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
import { AGENT_RESULT } from "@/components/ProviderPicker";
import {
  FormFooter,
  Icon,
  IconInput,
  LimitTile,
  ModelCard,
  ModelStatus,
  ProviderGrid,
  Step,
  UseTile,
} from "@/components/ModelSetup";
import { useMe } from "@/lib/useMe";
import { ApiError, api, type ModelPreset, type OwnLimits, type OwnModel } from "@/lib/api";

const NO_LIMITS: OwnLimits = { tokens_per_minute: 0, tokens_per_day: 0 };

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
  // The form to replace a saved model opens only when asked for.
  const [replacing, setReplacing] = useState(false);
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
      setReplacing(false);
      return "Saved. Pick it in the chat’s model menu.";
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
      setReplacing(false);
      setLimits(NO_LIMITS);
      setSavedLimits(NO_LIMITS);
      return "Removed. Your key is deleted.";
    });

  const limitsChanged =
    limits.tokens_per_minute !== savedLimits.tokens_per_minute || limits.tokens_per_day !== savedLimits.tokens_per_day;

  const limitTiles = (
    <div className="mk-limits is-two">
      <LimitTile
        id="tokens_per_minute"
        icon="clock"
        label="Per minute"
        value={limits.tokens_per_minute}
        onChange={(n) => setLimits({ ...limits, tokens_per_minute: n })}
        disabled={busy}
        note="Caps a burst of questions"
      />
      <LimitTile
        id="tokens_per_day"
        icon="calendar"
        label="Per day"
        value={limits.tokens_per_day}
        onChange={(n) => setLimits({ ...limits, tokens_per_day: n })}
        disabled={busy}
        note="Caps a whole day’s use"
      />
    </div>
  );

  return (
    <AppShell me={me}>
      <main className="page-wide studio-page stack mk-page">
        <PageHeader
          eyebrow="You"
          title="Your own model"
          description="Answer with the model you choose, on your own key. Only you can pick it, and you pay your provider directly."
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

        <ModelStatus
          saved={saved}
          scope="Only you can pick it"
          emptyTitle="You’re using your company’s models"
          emptyText="Add a key from any provider below. It’s tested first, then appears in your chat’s model menu."
          chips={saved?.agent ? [{ icon: "bolt", text: AGENT_RESULT[saved.agent], tone: saved.agent === "native" || saved.agent === "text" ? "ok" : "warn" }] : []}
          busy={busy}
          replacing={replacing}
          onReplace={() => setReplacing(true)}
          onRemove={remove}
        />

        {(!saved || replacing) && (
          <ModelCard
            icon="key"
            title={saved ? "Replace your model" : "Connect your key"}
            description="Three steps. We test the connection before anything is saved."
          >
            <form onSubmit={save} className="mk-steps">
              <Step n={1} title="Choose a provider">
                <ProviderGrid presets={presets} value={preset} onChange={setPreset} disabled={busy} />
              </Step>
              <Step n={2} title="Paste your API key" hint="Encrypted before it’s stored. Only its last four characters are shown afterwards.">
                <IconInput
                  icon="lock"
                  id="apiKey"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={`Your ${activePreset?.label ?? "provider"} API key`}
                  disabled={busy}
                  required
                  autoComplete="off"
                />
              </Step>
              <Step
                n={3}
                title="Enter the model ID"
                hint={
                  activePreset && (
                    <a className="mk-link" href={activePreset.models_url} target="_blank" rel="noreferrer">
                      Browse {activePreset.label} model IDs <Icon name="external" size={13} />
                    </a>
                  )
                }
              >
                <IconInput
                  icon="spark"
                  id="model"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="For example, claude-sonnet-5-5"
                  disabled={busy}
                  required
                />
              </Step>
              {!saved && (
                <Step n={4} title="Set limits (optional)" hint="A typical question uses 10,000 to 40,000 tokens. Leave 0 for no limit.">
                  {limitTiles}
                </Step>
              )}
              <FormFooter note="A short test runs first. Nothing is saved if it fails.">
                {saved && (
                  <button className="button button-secondary" type="button" onClick={() => setReplacing(false)} disabled={busy}>
                    Cancel
                  </button>
                )}
                <button className="button" type="submit" disabled={busy || !model.trim() || !apiKey.trim()}>
                  {busy ? "Testing connection…" : saved ? "Test and replace" : "Test and save"}
                </button>
              </FormFooter>
            </form>
          </ModelCard>
        )}

        {saved && (
          <ModelCard
            icon="gauge"
            title="Your limits"
            description="Optional caps on your own key, so a long day can’t run up your bill. A typical question uses 10,000 to 40,000 tokens."
          >
            {limitTiles}
            <FormFooter note="Applies from your next question.">
              <button className="button" type="button" onClick={saveLimits} disabled={busy || !limitsChanged}>
                {busy ? "Saving…" : "Save limits"}
              </button>
            </FormFooter>
          </ModelCard>
        )}

        <ModelCard icon="shield" title="How your key is used">
          <div className="mk-uses">
            <UseTile icon="chat" tag="On your key" title="Your questions">
              Answers when you pick it, and the checks around every question you ask on the web and in Slack DMs.
              The documents behind them go to {activePreset?.label ?? "your provider"}.
            </UseTile>
            <UseTile icon="doc" tag="Never on your key" title="Company work" tone="muted">
              Indexing your company&rsquo;s documents and everyone else&rsquo;s questions. Handbook&rsquo;s and your
              company&rsquo;s limits don&rsquo;t apply to your key.
            </UseTile>
          </div>
        </ModelCard>
      </main>
    </AppShell>
  );
}
