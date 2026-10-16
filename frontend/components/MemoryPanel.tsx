"use client";

import { useCallback, useEffect, useState } from "react";
import { MemoryFact, PersonalMemory, api } from "@/lib/api";

const KIND_LABELS: Record<MemoryFact["kind"], string> = {
  preference: "How you like answers",
  context: "About you",
  interest: "What you ask about",
};

/**
 * "What Handbook remembers about you".
 *
 * Facts come only from your own questions, never from answers or documents,
 * and they only help Handbook understand what you mean: they are never used
 * as a source. Unpinned facts go when the chat they came from goes (30 days);
 * pinning keeps one.
 */
export function MemoryPanel() {
  const [data, setData] = useState<PersonalMemory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .personalMemory()
      .then(setData)
      .catch(() => setError("Could not load what Handbook remembers."));
  }, []);

  useEffect(load, [load]);

  async function run(key: string, action: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await action();
      load();
    } catch {
      setError("That didn’t save. Try again.");
    } finally {
      setBusy(null);
    }
  }

  if (!data) {
    return error ? <p className="muted">{error}</p> : <p className="muted">Loading…</p>;
  }
  if (!data.available) return null;

  const active = data.org_enabled && data.enabled;
  return (
    <section id="memory" className="studio-panel account-setting-card memory-setting-card" aria-labelledby="memory-title">
      <div className="studio-section-head">
        <span className="account-setting-kicker">Personalisation</span>
        <h2 id="memory-title">What Handbook remembers about you</h2>
        <p className="muted">
          Small details from your questions help new chats understand you. They are private to
          you and are never used as answer sources.
        </p>
      </div>

      {error && (
        <div className="banner banner-warn" role="alert">
          {error}
        </div>
      )}

      {!data.org_enabled && (
        <p className="muted">Your company has turned memory off, so nothing is remembered.</p>
      )}

      <label className="memory-switch account-setting-toggle">
        <input
          type="checkbox"
          checked={data.enabled}
          disabled={busy === "switch" || !data.org_enabled}
          onChange={(e) => run("switch", () => api.setMemoryEnabled(e.target.checked))}
        />
        <span>
          <strong>Personal memory</strong>
          <small>Remember useful details from my questions.</small>
        </span>
      </label>

      {data.facts.length === 0 ? (
        <p className="account-memory-empty">
          {active ? "Nothing yet. Mention your team or office and it will show up here." : "Nothing remembered."}
        </p>
      ) : (
        <table className="gap-table">
          <thead>
            <tr>
              <th>Remembered</th>
              <th>Kind</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {data.facts.map((fact) => (
              <tr key={fact.id}>
                <td>{fact.text}</td>
                <td className="muted">{KIND_LABELS[fact.kind] ?? fact.kind}</td>
                <td className="memory-actions">
                  <button
                    type="button"
                    className="button button-secondary button-sm"
                    disabled={busy === fact.id}
                    title={fact.pinned ? "Let it expire with its chat" : "Keep it after its chat expires"}
                    onClick={() => run(fact.id, () => api.pinMemory(fact.id, !fact.pinned))}
                  >
                    {fact.pinned ? "Unpin" : "Pin"}
                  </button>
                  <button
                    type="button"
                    className="button button-secondary button-sm"
                    disabled={busy === fact.id}
                    onClick={() => run(fact.id, () => api.forgetMemory(fact.id))}
                  >
                    Forget
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {data.facts.length > 0 && (
        <div>
          <button
            type="button"
            className="button button-secondary button-sm"
            disabled={busy === "clear"}
            onClick={() => run("clear", () => api.clearMemory())}
          >
            Forget everything
          </button>
        </div>
      )}

      {data.can_manage_org && (
        <label className="memory-switch memory-switch-org account-setting-toggle">
          <input
            type="checkbox"
            checked={data.org_enabled}
            disabled={busy === "org"}
            onChange={(e) => run("org", () => api.setOrgMemoryEnabled(e.target.checked))}
          />
          <span>
            <strong>Company memory</strong>
            <small>Allow personal memory for everyone in the company.</small>
          </span>
        </label>
      )}
    </section>
  );
}
