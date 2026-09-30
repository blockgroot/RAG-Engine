"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError, SignInEmails, api } from "@/lib/api";

/**
 * Your sign-in email, and the ones you used before.
 *
 * Documents are shared with an email address, so changing yours would lose
 * everything shared with the old one. Handbook keeps the old address as a
 * prior email instead, and it keeps matching those documents for you.
 */
export function SignInEmailPanel() {
  const [data, setData] = useState<SignInEmails | null>(null);
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<{ ok: boolean; text: string; link?: string | null } | null>(
    null
  );
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .signInEmails()
      .then(setData)
      .catch(() => setStatus({ ok: false, text: "Could not load your sign-in email." }));
  }, []);

  useEffect(load, [load]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setStatus(null);
    try {
      const sent = await api.requestEmailChange(email.trim());
      setStatus({ ok: true, text: sent.message, link: sent.dev_link });
      setEmail("");
    } catch (err) {
      setStatus({
        ok: false,
        text: err instanceof ApiError ? err.message : "Could not send the confirmation link.",
      });
    } finally {
      setBusy(false);
    }
  }

  async function forget(address: string) {
    try {
      await api.removePriorEmail(address);
      load();
    } catch {
      setStatus({ ok: false, text: "Could not remove that address. Try again." });
    }
  }

  return (
    <section className="studio-panel stack" aria-labelledby="email-title">
      <div className="studio-section-head">
        <h2 id="email-title">Sign-in email</h2>
        <p className="muted">
          You sign in as <strong>{data?.email ?? "…"}</strong>. If you change it, we’ll send a
          link to the new address and switch once you click it. Documents shared with your old
          address stay visible to you.
        </p>
      </div>

      <form className="stack" onSubmit={submit}>
        <div className="field">
          <label htmlFor="new-email">New email</label>
          <input
            id="new-email"
            className="input"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <button className="button" type="submit" disabled={busy || !email.trim()}>
            {busy ? "Sending…" : "Send confirmation link"}
          </button>
        </div>
      </form>

      {status && (
        <div className={status.ok ? "banner banner-ok" : "banner banner-warn"} role="status">
          {status.text}
          {status.link && (
            <>
              {" "}
              <a href={status.link}>Open the link</a> (no email is sent in this environment).
            </>
          )}
        </div>
      )}

      {data && data.prior.length > 0 && (
        <div className="stack">
          <p className="muted">
            Earlier addresses. Documents shared with these still show up for you. Remove one if
            it’s no longer yours.
          </p>
          <ul className="stack">
            {data.prior.map((address) => (
              <li key={address}>
                {address}{" "}
                <button
                  type="button"
                  className="button button-secondary button-sm"
                  onClick={() => forget(address)}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
