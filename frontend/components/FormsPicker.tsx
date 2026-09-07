"use client";

/**
 * Which Google Forms may be read for survey sentiment.
 *
 * An ALLOW-LIST, not a filter over everything: a Drive token can see every
 * form in the account, so without an explicit choice this feature would read
 * an admin's unrelated personal surveys and turn them into company charts.
 * Nothing selected therefore means nothing is read.
 *
 * The picker lists forms through Drive, which the existing scope already
 * covers — so it works BEFORE the tenant reconnects for the responses scope.
 * That is deliberate: picking the surveys is step one, and being told to
 * reconnect is step two, in that order.
 */

import { useEffect, useState } from "react";

import { api, type GoogleForm } from "@/lib/api";

export function FormsPicker({
  connectionId,
  workspaceId,
  onSaved,
  onError,
}: {
  connectionId: string;
  workspaceId?: string;
  onSaved?: (selected: string[]) => void;
  onError?: (message: string) => void;
}) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [forms, setForms] = useState<GoogleForm[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    const load = workspaceId
      ? api.listWorkspaceConnectionForms(workspaceId, connectionId)
      : api.listConnectionForms(connectionId);
    load
      .then((payload) => {
        if (cancelled) return;
        setEnabled(payload.enabled);
        setForms(payload.forms);
        setSelected(new Set(payload.selected));
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "Could not list your forms.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [connectionId, workspaceId]);

  function toggle(id: string) {
    setSavedNote(null);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function save() {
    setSaving(true);
    onError?.("");
    try {
      const ids = Array.from(selected);
      const result = workspaceId
        ? await api.setWorkspaceConnectionForms(workspaceId, connectionId, ids)
        : await api.setConnectionForms(connectionId, ids);
      setSelected(new Set(result.selected));
      setSavedNote(
        result.selected.length === 0
          ? "Saved. No surveys are being read."
          : `Saved. ${result.selected.length} survey${result.selected.length === 1 ? "" : "s"} will be read on the next sync.`
      );
      onSaved?.(result.selected);
    } catch (err) {
      onError?.(err instanceof Error ? err.message : "Could not save that selection.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="muted">Loading your forms&hellip;</p>;

  // A deployment that has not switched the scope on. Said plainly, with the
  // cost named: enabling it changes the Google OAuth scope, so every already-
  // connected tenant has to reconnect before a response can be read.
  if (enabled === false) {
    return (
      <div className="forms-picker">
        <p className="muted" style={{ margin: 0 }}>
          Survey reading is switched off for this deployment. An operator turns it on
          with <code>GOOGLE_FORMS_ENABLED</code>, which adds one Google permission
          &mdash; everyone who has already connected Google needs to reconnect once
          before any response can be read.
        </p>
      </div>
    );
  }

  if (loadError) return <div className="banner banner-warn">{loadError}</div>;

  return (
    <div className="forms-picker stack">
      <p className="muted" style={{ margin: 0 }}>
        Pick the surveys to measure. Each answer is read once, labelled, and the
        text is thrown away &mdash; no answer, and no respondent, is ever stored or
        shown. Nothing selected means nothing is read.
      </p>

      {forms.length === 0 ? (
        <p className="muted">
          No forms visible to this Google connection yet. Create one in Google Forms,
          or check the account you connected, then reopen this.
        </p>
      ) : (
        <div className="forms-list">
          {forms.map((form) => (
            <label key={form.id} className="forms-row">
              <input
                type="checkbox"
                checked={selected.has(form.id)}
                onChange={() => toggle(form.id)}
              />
              <span className="forms-row-title">{form.title}</span>
            </label>
          ))}
        </div>
      )}

      <div className="forms-actions">
        <button className="button" type="button" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save selection"}
        </button>
        {savedNote && <span className="muted">{savedNote}</span>}
      </div>

      <p className="muted forms-foot">
        Charts stay owners-only, and a question with fewer than five responses is
        never charted &mdash; a sentiment chart of three people is a chart of who
        said it.
      </p>
    </div>
  );
}
