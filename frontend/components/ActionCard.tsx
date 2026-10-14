"use client";

import { useEffect, useState } from "react";

import { ApiError, api, type ActCard } from "@/lib/api";

/**
 * The approval card for one proposed write.
 *
 * It shows exactly what will be sent and where, because the server sends the
 * stored copy and nothing else: Approve returns only this card's fingerprint.
 * When the draft used private documents that some people at the destination
 * cannot see, it says so with numbers, and Approve stays off until the person
 * ticks that they understand. Nothing here can change the payload.
 */

const PROVIDER_LABEL: Record<string, string> = {
  linear: "Linear",
  github: "GitHub",
  notion: "Notion",
  google: "Google Drive",
  slack: "Slack",
};

const STATUS_LINE: Record<ActCard["status"], string> = {
  proposed: "Waiting for your approval",
  executing: "Sending…",
  succeeded: "Done",
  failed: "Not done",
  unknown: "May have gone through. Check before trying again.",
  rejected: "Cancelled",
  expired: "Expired. Ask again for a fresh card.",
};

function remaining(expiresAt: string, now: number): number {
  return Math.max(0, Math.floor((new Date(expiresAt).getTime() - now) / 1000));
}

/** A source as a short label: a Slack thread's title is its first message. */
function shortLabel(title: string): string {
  return title.length > 60 ? `${title.slice(0, 57).trimEnd()}…` : title;
}

export function ActionCard({
  card,
  onChange,
}: {
  card: ActCard;
  onChange: (next: ActCard) => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pending = card.status === "proposed";
  useEffect(() => {
    if (!pending) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [pending]);

  const secondsLeft = remaining(card.expires_at, now);
  const expired = pending && secondsLeft === 0;
  const needsAck = card.exposure === "wider" || card.exposure === "unknown";
  const provider = PROVIDER_LABEL[card.provider ?? ""] ?? card.provider ?? "the tool";

  async function approve() {
    setBusy(true);
    setError(null);
    try {
      const { action } = await api.confirmAction(card.id, card.payload_hash, ack);
      onChange(action);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send it. Nothing was sent.");
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    setBusy(true);
    setError(null);
    try {
      const { action } = await api.rejectAction(card.id);
      onChange(action);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not cancel it.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="act-card" data-status={card.status} aria-label={card.label}>
      <header className="act-card-head">
        <span className="act-card-label">{card.label}</span>
        <span className="act-card-as">
          {card.attribution ?? "as you"}, in {provider}
        </span>
      </header>

      <dl className="act-card-fields">
        <dt>Where</dt>
        <dd>
          {card.target.url ? (
            <a
              className="act-card-target-link"
              href={card.target.url}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open ${card.target.name} in ${provider}`}
            >
              {card.target.name}
            </a>
          ) : (
            card.target.name
          )}
        </dd>
        {card.payload.title !== undefined && (
          <>
            <dt>Title</dt>
            <dd className="act-card-title">{card.payload.title}</dd>
          </>
        )}
        {card.payload.body !== undefined && (
          <>
            <dt>{card.payload.title !== undefined ? "Description" : "Comment"}</dt>
            <dd className="act-card-body">{card.payload.body || <span className="muted">(empty)</span>}</dd>
          </>
        )}
      </dl>

      {needsAck && pending && (
        <div className="act-card-exposure" role="alert">
          <p>
            This draft may include private content only some people can see, from{" "}
            {card.sources.map(shortLabel).join("; ")}.{" "}
            {card.exposure === "wider" ? (
              <strong>
                {card.audience_size ?? "Some"} people can open where it&rsquo;s going, and{" "}
                {card.outsiders ?? "some"} of them can&rsquo;t see that content today.
              </strong>
            ) : (
              <strong>
                I can&rsquo;t check who can open where it&rsquo;s going, so people who can&rsquo;t see
                that content might read it there.
              </strong>
            )}
          </p>
          <label className="act-card-ack">
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            I&rsquo;ve read the draft and I&rsquo;m OK sharing it there.
          </label>
        </div>
      )}

      {pending && !expired ? (
        <div className="act-card-actions">
          <button
            type="button"
            className="button"
            onClick={approve}
            disabled={busy || (needsAck && !ack)}
          >
            {busy ? "Sending…" : "Approve"}
          </button>
          <button type="button" className="button button-secondary" onClick={cancel} disabled={busy}>
            Cancel
          </button>
          <span className="muted act-card-timer" aria-live="off">
            Expires in {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, "0")}
          </span>
        </div>
      ) : (
        <p className="act-card-status">
          <span className="studio-chip">{expired ? STATUS_LINE.expired : STATUS_LINE[card.status]}</span>
          {card.status === "succeeded" && card.result?.url && (
            <a className="act-card-result-link" href={card.result.url} target="_blank" rel="noreferrer">
              Open in {provider}
            </a>
          )}
          {(card.status === "failed" || card.status === "unknown") && card.result?.error && (
            <span className="muted"> {card.result.error}</span>
          )}
        </p>
      )}

      {error && (
        <p className="act-card-error" role="alert">
          {error}
        </p>
      )}
    </article>
  );
}
