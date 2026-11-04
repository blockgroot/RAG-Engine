"use client";

/**
 * Asks once what a person wants to be called.
 *
 * The name used to be guessed from the email ("sana.asiwal" -> "Sana"), which
 * is wrong for addresses like "s.asiwal" or "hr", so it is asked instead,
 * starting from that guess so most people only press Save.
 */

import { useState } from "react";

import { api, ApiError } from "@/lib/api";

export function NamePrompt({ guess, onSaved }: { guess: string; onSaved: (name: string) => void }) {
  const [name, setName] = useState(guess);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="name-prompt-backdrop">
      <form
        className="card name-prompt"
        role="dialog"
        aria-modal="true"
        aria-labelledby="name-prompt-title"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          api
            .setName(name)
            .then((saved) => onSaved(saved.name))
            .catch((err) => setError(err instanceof ApiError ? err.message : "Could not save. Try again."))
            .finally(() => setBusy(false));
        }}
      >
        <h2 id="name-prompt-title">What should we call you?</h2>
        <p className="muted">This is how your name shows in Handbook. You can change it later in Account.</p>
        <div className="field">
          <label htmlFor="name-prompt-input">Your name</label>
          <input
            id="name-prompt-input"
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={60}
            autoComplete="name"
            autoFocus
          />
        </div>
        {error && <div className="banner banner-warn">{error}</div>}
        <button className="button" type="submit" disabled={busy || !name.trim()}>
          {busy ? "Saving…" : "Save"}
        </button>
      </form>
    </div>
  );
}
