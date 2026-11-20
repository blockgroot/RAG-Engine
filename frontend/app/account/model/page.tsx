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
import {
  FormFooter,
  Icon,
  IconInput,
  LimitTile,
  ModelCard,
  ModelStatus,
  ProviderGrid,
  TypedModelIds,
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
  // Set only when the provider publishes no model list: then IDs are typed.
  const [typeIds, setTypeIds] = useState(false);
  const [typed, setTyped] = useState("");
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
      const ids = typeIds ? typed.split(/[\s,]+/).filter(Boolean) : [];
      const res = await api.saveOwnModel(preset, apiKey.trim(), limits, ids);
      if (!res.model) {
        setTypeIds(true);
        return res.type_ids ?? "This provider doesn’t list its models. Type the model IDs.";
      }
      const next = res.model;
      setSaved(next);
      setTypeIds(false);
      setTyped("");
      setSavedLimits(limits);
      setApiKey("");
      setReplacing(false);
      return `Saved with ${next.models.length} ${next.models.length === 1 ? "model" : "models"}. Your key is now used everywhere: chat, Slack and your reports.`;
    });
  };

  const setUse = (on: boolean) =>
    run(async () => {
      const { model: next } = await api.setOwnModelUse(on);
      setSaved(next);
      return on
        ? "Your key is used everywhere: chat, Slack and your reports."
        : "Your company’s key is used everywhere. Your key is kept but unused.";
    });

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
          emptyText="Add a key from any provider below. Every chat model it offers appears in your chat’s model menu."
          chips={saved ? [{ icon: "bolt", text: saved.use_everywhere ? "Used everywhere" : "Switched off", tone: saved.use_everywhere ? "ok" : "warn" }] : []}
          busy={busy}
          replacing={replacing}
          onReplace={() => setReplacing(true)}
          onRemove={remove}
        />

        {(!saved || replacing) && (
          <ModelCard
            icon="key"
            title={saved ? "Replace your model" : "Connect your key"}
            description="We check the key with the provider before anything is saved."
          >
            <form onSubmit={save} className="mk-steps">
              <Step n={1} title="Choose a provider">
                <ProviderGrid presets={presets} value={preset} onChange={(p) => { setPreset(p); setTypeIds(false); }} disabled={busy} />
              </Step>
              <Step n={2} title="Paste your API key" hint="Encrypted before it’s stored. Only its last four characters are shown afterwards.">
                <IconInput
                  icon="lock"
                  id="apiKey"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={saved && saved.preset === preset ? `Leave blank to keep the key ending ${saved.key_tail}` : `Your ${activePreset?.label ?? "provider"} API key`}
                  disabled={busy}
                  required={!(saved && saved.preset === preset)}
                  autoComplete="off"
                />
              </Step>
              {typeIds && (
                <Step n={3} title="Type the model IDs">
                  <TypedModelIds preset={activePreset} value={typed} onChange={setTyped} disabled={busy} />
                </Step>
              )}
              {!saved && (
                <Step n={typeIds ? 4 : 3} title="Set limits (optional)" hint="A typical question uses 10,000 to 40,000 tokens. Leave 0 for no limit.">
                  {limitTiles}
                </Step>
              )}
              <FormFooter note="We read the key’s model list from the provider; every chat model on it appears in your model menu.">
                {saved && (
                  <button className="button button-secondary" type="button" onClick={() => setReplacing(false)} disabled={busy}>
                    Cancel
                  </button>
                )}
                <button className="button" type="submit" disabled={busy || (typeIds && !typed.trim())}>
                  {busy ? "Reading the key’s models…" : saved ? "Save" : "Save and use everywhere"}
                </button>
              </FormFooter>
            </form>
          </ModelCard>
        )}

        {saved && (
          <ModelCard
            icon="bolt"
            title="Where your key is used"
            description="One switch for everything you do. Indexing your company’s documents always uses your company’s key."
          >
            <label className="mk-toggle">
              <input
                type="checkbox"
                role="switch"
                checked={saved.use_everywhere}
                onChange={(e) => void setUse(e.target.checked)}
                disabled={busy}
              />
              <span>
                <strong>Use my own key everywhere</strong>
                <small>
                  {saved.use_everywhere
                    ? "On: your chat answers, Slack replies and the reports you schedule run on your key."
                    : "Off: your company’s key is used everywhere. Your key is kept, unused."}
                </small>
              </span>
            </label>
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
            <UseTile icon="chat" tag="On your key, when switched on" title="Everything you do">
              Your chat answers, your Slack questions (DMs and channels) and the reports you schedule. The
              documents behind them go to {activePreset?.label ?? "your provider"}.
            </UseTile>
            <UseTile icon="doc" tag="Never on your key" title="Company work" tone="muted">
              Indexing your company&rsquo;s documents and everyone else&rsquo;s questions. Handbook&rsquo;s and your
              company&rsquo;s limits don&rsquo;t apply to your key; only the ones you set here.
            </UseTile>
          </div>
        </ModelCard>
      </main>
    </AppShell>
  );
}
