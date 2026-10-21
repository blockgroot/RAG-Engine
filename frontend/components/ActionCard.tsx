"use client";

import { useEffect, useState } from "react";

import { ApiError, api, type ActCard } from "@/lib/api";
import { pickFolder } from "@/lib/googlePicker";
import { DraftText } from "./AnswerText";

/**
 * The approval card for one proposed write.
 *
 * It shows exactly what will be sent and where, because the server sends the
 * stored copy and nothing else: Approve returns only this card's fingerprint.
 * When the draft used private documents that some people at the destination
 * cannot see, it says so with numbers, and Approve stays off until the person
 * ticks that they understand. Edit sends the person's own words to the
 * server, which stores them and returns a new fingerprint; Approve stays off
 * while an edit is unsaved, so only what was saved and shown can be sent.
 * "Ask for changes" sends an instruction instead ("make it shorter", "add
 * the steps to reproduce"): the model rewrites the words, the server saves
 * them like an edit, and only links and @-mentions already on the card or in
 * the instruction survive. Where it goes is never editable: cancel and ask
 * again for that.
 */

const PROVIDER_LABEL: Record<string, string> = {
  linear: "Linear",
  github: "GitHub",
  notion: "Notion",
  google: "Google Drive",
  slack: "Slack",
};

const APP_LABEL: Record<string, string> = {
  "drive.create_doc": "Google Docs",
  "drive.create_sheet": "Google Sheets",
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
  model,
}: {
  card: ActCard;
  onChange: (next: ActCard) => void;
  /** The chat's model pick, used for "Ask for changes". */
  model?: string | null;
}) {
  const [now, setNow] = useState(() => Date.now());
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ title?: string; body?: string } | null>(null);
  const [asking, setAsking] = useState<string | null>(null);

  const pending = card.status === "proposed";
  useEffect(() => {
    if (!pending) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [pending]);

  const secondsLeft = remaining(card.expires_at, now);
  const expired = pending && secondsLeft === 0;
  const needsAck = card.exposure === "wider" || card.exposure === "unknown";
  // An MCP tool's label names its server ("Asana: Create task").
  const provider = PROVIDER_LABEL[card.provider ?? ""] ?? (card.label.split(":")[0] || "the tool");
  // A Doc or Sheet is stored in Drive but opens in its own app.
  const openIn = APP_LABEL[card.action] ?? provider;
  const draft = card.draft ?? card.payload;
  const bodyLabel =
    card.action === "drive.create_sheet"
      ? "Table"
      : draft.title !== undefined
        ? "Description"
        : card.provider === "slack"
          ? "Message"
          : "Comment";

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

  async function saveEdit() {
    if (!editing) return;
    const fields: { title?: string; body?: string } = {};
    if (editing.title !== undefined && editing.title !== draft.title) fields.title = editing.title;
    if (editing.body !== undefined && editing.body !== draft.body) fields.body = editing.body;
    if (Object.keys(fields).length === 0) {
      setEditing(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { action } = await api.editAction(card.id, card.payload_hash, fields);
      setEditing(null);
      setAck(false);
      onChange(action);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save your edit. Nothing was sent.");
    } finally {
      setBusy(false);
    }
  }

  async function revise() {
    const instruction = asking?.trim();
    if (!instruction) return;
    setBusy(true);
    setError(null);
    try {
      const { action } = await api.reviseAction(card.id, card.payload_hash, instruction, model);
      setAsking(null);
      setAck(false);
      onChange(action);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not rework it. Your draft is unchanged.");
    } finally {
      setBusy(false);
    }
  }

  async function chooseFolder() {
    setBusy(true);
    setError(null);
    try {
      const folderId = await pickFolder(await api.googlePicker());
      if (folderId) {
        const { action } = await api.chooseActionFolder(card.id, card.payload_hash, folderId);
        setAck(false);
        onChange(action);
      }
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : "Could not choose the folder.");
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
          {card.attribution ?? "as you"}, in {openIn}
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
        {/* Settings the write carries (assignee, labels, due date...): shown,
            never editable. To change one, cancel and ask again. */}
        {card.payload.details?.map(([label, value]) => (
          <div key={label} style={{ display: "contents" }}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
        {draft.title !== undefined && (
          <>
            <dt>
              <label htmlFor={`act-title-${card.id}`}>Title</label>
            </dt>
            <dd className="act-card-title">
              {editing ? (
                <input
                  id={`act-title-${card.id}`}
                  className="input act-card-edit-title"
                  maxLength={255}
                  value={editing.title ?? ""}
                  onChange={(e) => setEditing({ ...editing, title: e.target.value })}
                />
              ) : (
                draft.title
              )}
            </dd>
          </>
        )}
        {draft.body !== undefined && (
          <>
            <dt>
              <label htmlFor={`act-body-${card.id}`}>{bodyLabel}</label>
            </dt>
            <dd className="act-card-body">
              {editing ? (
                <textarea
                  id={`act-body-${card.id}`}
                  className="input act-card-edit-body"
                  rows={Math.min(12, Math.max(4, (editing.body ?? "").split("\n").length + 1))}
                  maxLength={8000}
                  value={editing.body ?? ""}
                  onChange={(e) => setEditing({ ...editing, body: e.target.value })}
                />
              ) : (
                draft.body ? <DraftText text={draft.body} /> : <span className="muted">(empty)</span>
              )}
            </dd>
          </>
        )}
      </dl>
      {card.edited && !editing && <p className="muted act-card-edited">Edited by you</p>}
      {card.provider === "notion" && pending && (
        // Notion can't be asked in advance what someone may do on a page, so
        // the card says what the write needs; Notion refuses it otherwise.
        <p className="muted act-card-note">
          Notion doesn&rsquo;t let apps check your access first. This works only if you can{" "}
          {card.action === "notion.comment" ? "comment on" : "edit"} this page in Notion.
        </p>
      )}

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

      {pending && !expired && asking !== null ? (
        <div className="act-card-actions">
          <input
            className="input act-card-ask"
            aria-label="What should change?"
            placeholder="e.g. make it shorter, add steps to reproduce"
            maxLength={1000}
            value={asking}
            autoFocus
            onChange={(e) => setAsking(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void revise();
              if (e.key === "Escape") setAsking(null);
            }}
          />
          <button type="button" className="button" onClick={revise} disabled={busy || !asking.trim()}>
            {busy ? "Reworking…" : "Rework"}
          </button>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => setAsking(null)}
            disabled={busy}
          >
            Never mind
          </button>
        </div>
      ) : pending && !expired && editing ? (
        <div className="act-card-actions">
          <button type="button" className="button" onClick={saveEdit} disabled={busy}>
            {busy ? "Saving…" : "Save changes"}
          </button>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => setEditing(null)}
            disabled={busy}
          >
            Discard changes
          </button>
          <span className="muted act-card-timer">To change where it goes, cancel and ask again.</span>
        </div>
      ) : pending && !expired ? (
        <div className="act-card-actions">
          <button
            type="button"
            className="button"
            onClick={approve}
            disabled={busy || (needsAck && !ack)}
          >
            {busy ? "Sending…" : "Approve"}
          </button>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => setEditing({ ...draft })}
            disabled={busy}
          >
            Edit
          </button>
          <button
            type="button"
            className="button button-secondary"
            onClick={() => setAsking("")}
            disabled={busy}
          >
            Ask for changes
          </button>
          {card.folder_choice && (
            <button type="button" className="button button-secondary" onClick={chooseFolder} disabled={busy}>
              Choose folder
            </button>
          )}
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
              Open in {openIn}
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
