"use client";

/**
 * Google Forms as its own card, on the SAME Google connection.
 *
 * The split is presentational and deliberately stops there. Surveys are not a
 * separate connector and cannot become one: they authenticate with the same
 * Google account, the same stored token, and the picker lists forms THROUGH
 * Drive (`files.list`), so a second connection would mean a second consent
 * screen and a duplicate token for one account — with the two silently able to
 * point at different Google accounts.
 *
 * What was genuinely confusing is that one card meant two unrelated decisions:
 * which folder gets INDEXED and answers questions, and which surveys get
 * READ ONCE for a sentiment label and are never indexed at all. Those have
 * different consequences, so they are now two cards, and this one says what it
 * writes to.
 */

import { useState } from "react";

import { BrandGlyph } from "./BrandGlyph";
import { FormsPicker } from "./FormsPicker";
import type { ConnectionRecord } from "@/lib/api";

export function FormsCard({
  connection,
  workspaceId,
  onConfigSaved,
}: {
  /** The GOOGLE connection. Undefined when Google is not connected. */
  connection?: ConnectionRecord;
  workspaceId?: string;
  onConfigSaved?: (updated: ConnectionRecord) => void;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = (connection?.source_config?.form_ids ?? []).length;

  return (
    <div className={`card source-row source-row--forms${connection ? " is-linked" : ""}`}>
      <div className="source-row-head">
        <div className="source-row-main">
          <span className="source-row-mark">
            <BrandGlyph name="forms" size={28} />
          </span>
          <div className="source-row-copy">
            <h3>Google Forms</h3>
            <p className="source-row-kind">
              Survey sentiment
              {connection ? (
                <>
                  {" · "}
                  {selected > 0
                    ? `${selected} survey${selected === 1 ? "" : "s"} selected`
                    : "None selected"}
                </>
              ) : null}
            </p>
          </div>
        </div>

        <div className="source-row-actions">
          {connection ? (
            <button
              className="button button-secondary"
              type="button"
              onClick={() => {
                setOpen((prev) => !prev);
                setError(null);
              }}
            >
              {open ? "Hide surveys" : selected > 0 ? "Change surveys" : "Choose surveys"}
            </button>
          ) : null}
        </div>
      </div>

      <p className="muted" style={{ margin: "0.65rem 0 0" }}>
        {connection
          ? "Read once for a positive-or-negative label, then the answer text is thrown away. Never indexed, so a survey answer can never come back in a chat answer."
          : "Uses your Google connection above — connect Google Drive first, then pick which surveys to measure."}
      </p>

      {connection && open && (
        <div className="stack" style={{ marginTop: "0.9rem" }}>
          <FormsPicker
            connectionId={connection.id}
            workspaceId={workspaceId}
            onSaved={(ids) => {
              setError(null);
              onConfigSaved?.({
                ...connection,
                source_config: { ...connection.source_config, form_ids: ids },
              });
            }}
            onError={(message) => setError(message || null)}
          />
          {error && <div className="banner banner-warn">{error}</div>}
        </div>
      )}
    </div>
  );
}
