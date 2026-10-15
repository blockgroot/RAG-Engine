"use client";

/**
 * A space owner's Act switch. Only narrows what the company allows: shown but
 * locked while the company has Act off (so the owner can find it and knows
 * why), and the owner's only choice is whether
 * people may use Act in this space. What each person may then do in a tool is
 * still their own account's access there.
 */

import { useEffect, useState } from "react";

import { ApiError, api, type SpaceAct } from "@/lib/api";

export function SpaceActPanel({ workspaceId }: { workspaceId: string }) {
  const [state, setState] = useState<SpaceAct | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.spaceAct(workspaceId).then(setState).catch(() => setState(null));
  }, [workspaceId]);

  if (!state) return null;
  const companyOff = state.company === "off";

  async function change(enabled: boolean) {
    setBusy(true);
    setError(null);
    try {
      setState(await api.setSpaceAct(workspaceId, enabled));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change that.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="studio-panel stack" aria-labelledby="space-act-title">
      <div className="studio-section-head">
        <h2 id="space-act-title">Act in this space</h2>
        <p className="muted">
          Whether members may ask Handbook to make changes in their tools from this space. Each
          change still runs on the member&rsquo;s own account and waits for their approval.
        </p>
      </div>
      <label className="action-surface">
        <span>
          <strong>Allow Act in this space</strong>
          <small>
            {companyOff
              ? "Your admin has turned Act off for the company (Admin › Actions). You can switch it on here once they allow it."
              : state.enabled
              ? "Members can ask Handbook to file issues, comment and post from this space, using their own accounts, after they approve each one."
              : "Off for this space. Your admin allows Act; you turned it off here."}
          </small>
        </span>
        <input
          type="checkbox"
          checked={state.enabled}
          disabled={busy || !state.can_change}
          onChange={(e) => void change(e.target.checked)}
          aria-label={state.enabled ? "Turn off Act in this space" : "Allow Act in this space"}
        />
      </label>
      {error && (
        <p className="act-card-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
