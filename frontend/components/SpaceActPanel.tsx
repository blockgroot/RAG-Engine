"use client";

/**
 * A space owner's Act switch. Only narrows what the company allows: hidden
 * while the company has Act off, and the owner's only choice is whether
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

  if (!state || !state.can_change) return null;

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
      <label className="action-surface">
        <span>
          <strong id="space-act-title">Allow Act in this space</strong>
          <small>
            {state.enabled
              ? "Members can ask Handbook to file issues, comment and post from this space, using their own accounts, after they approve each one."
              : state.company === "spaces"
                ? "Off until you turn it on. Your admin lets each space decide."
                : "Off for this space. Your admin allows Act; you turned it off here."}
          </small>
        </span>
        <input
          type="checkbox"
          checked={state.enabled}
          disabled={busy}
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
