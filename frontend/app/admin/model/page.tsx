"use client";

/**
 * Admin-only: connect the organisation's own model, and set its limits.
 * Says what runs on the company's key (answers when picked, the checks around
 * questions, indexing and other company work) and what never does (search and
 * ranking): implying full isolation would mislead.
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
import { ApiError, api, type AiLimits, type ModelPreset, type OrgModel } from "@/lib/api";

const LIMIT_GROUPS: { title: string; icon: "person" | "building"; rows: { key: keyof AiLimits; icon: "clock" | "calendar"; label: string }[] }[] = [
  {
    title: "Each person",
    icon: "person",
    rows: [
      { key: "person_tokens_per_minute", icon: "clock", label: "Per minute" },
      { key: "person_tokens_per_day", icon: "calendar", label: "Per day" },
    ],
  },
  {
    title: "Whole company",
    icon: "building",
    rows: [
      { key: "company_tokens_per_minute", icon: "clock", label: "Per minute" },
      { key: "company_tokens_per_day", icon: "calendar", label: "Per day" },
    ],
  },
];

/**
 * The company's own AI limits, in tokens. They narrow Handbook's limits on the
 * built-in model and are the only limits on the company's model. A person's own
 * key is never limited. 0 = no limit.
 */
function AiLimitsPanel() {
  const [limits, setLimits] = useState<AiLimits | null>(null);
  const [defaults, setDefaults] = useState<AiLimits | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api
      .getAiLimits()
      .then(({ limits: current, defaults: preset }) => {
        setLimits(current);
        setDefaults(preset);
      })
      .catch((err) => setMessage({ ok: false, text: err instanceof ApiError ? err.message : "Could not load the limits." }));
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!limits) return;
    setBusy(true);
    setMessage(null);
    try {
      const { limits: next } = await api.saveAiLimits(limits);
      setLimits(next);
      setMessage({ ok: true, text: "Saved. The new limits apply from the next question." });
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : "Could not save the limits." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <ModelCard
      icon="gauge"
      title="Usage limits"
      description={
        <>
          How much AI each person and the whole company may use, on Handbook&rsquo;s model and on your company&rsquo;s.
          A typical question uses 10,000 to 40,000 tokens. A person&rsquo;s own key is never limited.
        </>
      }
    >
      {message && (
        <div className={`banner ${message.ok ? "banner-ok" : "banner-warn"}`} role={message.ok ? "status" : "alert"}>
          {message.text}
        </div>
      )}
      {!limits ? (
        <p className="muted">Loading…</p>
      ) : (
        <form onSubmit={save} className="mk-limit-groups">
          {LIMIT_GROUPS.map((group) => (
            <div className="mk-limit-group" key={group.title}>
              <h3>
                <Icon name={group.icon} size={16} /> {group.title}
              </h3>
              <div className="mk-limits is-two">
                {group.rows.map((row) => (
                  <LimitTile
                    key={row.key}
                    id={row.key}
                    icon={row.icon}
                    label={row.label}
                    value={limits[row.key]}
                    onChange={(n) => setLimits({ ...limits, [row.key]: n })}
                    disabled={busy}
                    note={defaults && limits[row.key] === defaults[row.key] ? "Handbook default" : "Custom limit"}
                  />
                ))}
              </div>
            </div>
          ))}
          <FormFooter note="Applies from the next question. 0 means no limit.">
            <button className="button" type="submit" disabled={busy}>
              {busy ? "Saving…" : "Save limits"}
            </button>
          </FormFooter>
        </form>
      )}
    </ModelCard>
  );
}

export default function AdminModelPage() {
  const { me, loading } = useMe({ requireAdmin: true });

  const [presets, setPresets] = useState<ModelPreset[]>([]);
  const [saved, setSaved] = useState<OrgModel | null>(null);

  const [preset, setPreset] = useState("openai");
  // Set only when the provider publishes no model list: then IDs are typed.
  const [typeIds, setTypeIds] = useState(false);
  const [typed, setTyped] = useState("");
  const [apiKey, setApiKey] = useState("");

  const [busy, setBusy] = useState(false);
  // The form to replace a saved model opens only when asked for.
  const [replacing, setReplacing] = useState(false);
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

  if (loading || !me) return <PageLoader />;

  const activePreset = presets.find((p) => p.id === preset);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const ids = typeIds ? typed.split(/[\s,]+/).filter(Boolean) : [];
      const res = await api.saveOrgModel(preset, apiKey.trim(), ids);
      if (!res.model) {
        setTypeIds(true);
        setNotice(res.type_ids ?? "This provider doesn’t list its models. Type the model IDs.");
        return;
      }
      const next = res.model;
      setSaved(next);
      setApiKey("");
      setTypeIds(false);
      setTyped("");
      setReplacing(false);
      setNotice(`Saved. Everyone in your company can now pick from ${next.models.length} ${next.models.length === 1 ? "model" : "models"} in chat.`);
    } catch (err) {
      // The provider's own words: 401 (wrong key) vs 404 (wrong model id).
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
      setReplacing(false);
      setNotice("Removed. Everyone is back to the built-in models.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove that.");
    } finally {
      setBusy(false);
    }
  }

  const chips: { icon: "check" | "bolt"; text: string; tone?: "ok" | "warn" }[] = [];
  if (saved?.checked_at) chips.push({ icon: "check", text: `Key checked ${new Date(saved.checked_at).toLocaleDateString()}`, tone: "ok" });

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack mk-page">
        <PageHeader
          eyebrow="Company"
          title="Company model and limits"
          description="Connect your company’s key, every chat model it offers appears in your team’s model menu. Set how much AI each person and the company may use."
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
          scope={`Everyone at ${me.org_name} can pick it`}
          emptyTitle="Your team uses Handbook’s built-in models"
          emptyText="Connect your company’s own key below, and every chat model it offers appears in everyone’s model menu."
          chips={chips}
          busy={busy}
          replacing={replacing}
          onReplace={() => setReplacing(true)}
          onRemove={remove}
        />

        {(!saved || replacing) && (
          <ModelCard
            icon="key"
            title={saved ? "Replace the company model" : "Connect your company’s key"}
            description="We check the key with the provider before your team sees anything."
          >
            <form onSubmit={save} className="mk-steps">
              <Step n={1} title="Choose a provider">
                <ProviderGrid presets={presets} value={preset} onChange={(p) => { setPreset(p); setTypeIds(false); }} disabled={busy} />
              </Step>
              <Step n={2} title="Paste the API key" hint="Encrypted before it’s stored. Only its last four characters are shown afterwards.">
                <IconInput
                  icon="lock"
                  id="apiKey"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={saved && saved.preset === preset ? `Leave blank to keep the key ending ${saved.key_tail}` : `Your company’s ${activePreset?.label ?? "provider"} API key`}
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
              <FormFooter note="We read the key’s model list from the provider; every chat model on it appears in the model menu.">
                {saved && (
                  <button className="button button-secondary" type="button" onClick={() => setReplacing(false)} disabled={busy}>
                    Cancel
                  </button>
                )}
                <button className="button" type="submit" disabled={busy || (typeIds && !typed.trim())}>
                  {busy ? "Reading the key’s models…" : saved ? "Save" : "Connect"}
                </button>
              </FormFooter>
            </form>
          </ModelCard>
        )}

        <AiLimitsPanel />

        <ModelCard
          icon="shield"
          title="How the company key is used"
          description="Search and ranking always stay on Handbook."
        >
          <div className="mk-uses">
            <UseTile icon="chat" tag="On the company key" title="Answers and checks">
              Answers when someone picks it, and the checks around every question, unless that person added their own
              key. Billed by {activePreset?.label ?? "your provider"}.
            </UseTile>
            <UseTile icon="doc" tag="On the company key" title="Indexing and company work">
              Context for every synced document, figures read from text, grouping unanswered questions and reports.
              It runs on every sync, so a large model costs more here.
            </UseTile>
          </div>
          <p className="mk-fallback">
            <Icon name="check" size={15} /> Without a key, or if it&rsquo;s removed, everything runs on Handbook&rsquo;s
            model, and members can always switch to it in chat.
          </p>
        </ModelCard>
      </main>
    </AppShell>
  );
}
