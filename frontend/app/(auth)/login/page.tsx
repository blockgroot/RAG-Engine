"use client";

import { useState } from "react";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";
import { api, ApiError, MagicLinkResponse } from "@/lib/api";

function SuccessMark() {
  return (
    <div className="auth-v2-success-icon" aria-hidden>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
        <path
          d="M5 13l4 4L19 7"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [result, setResult] = useState<MagicLinkResponse | null>(null);
  const [sentTo, setSentTo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const response = await api.requestMagicLink(email);
      setSentTo(email);
      setResult(response);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  function reset() {
    setResult(null);
    setError(null);
  }

  return (
    <AuthShell
      variant="login"
      footer={
        <p>
          First time here? <Link href="/signup">Set up your company</Link>
        </p>
      }
    >
      <div className="auth-v2-card-head">
        <p className="auth-v2-card-kicker">Welcome back</p>
        <h2>Sign in to Handbook</h2>
        <p className="muted">
          Enter your work email — we&rsquo;ll send a one-time link. No password
          needed.
        </p>
      </div>

      {result?.status === "sent" ? (
        <div className="auth-v2-success stack">
          <SuccessMark />
          <p>
            Sign-in link sent to <strong>{sentTo}</strong>. It expires shortly
            and works once.
          </p>
          {result.dev_link && (
            <p className="muted">
              Dev mode: <a href={result.dev_link}>Continue to sign in</a>
            </p>
          )}
        </div>
      ) : result?.status === "no_account" ? (
        /*
          The case this whole change exists for. Previously this rendered the
          same "check your inbox" as a success, so someone whose company had
          not onboarded waited for an email that was never sent.

          Note the wording: "no account for this email", NOT "your organisation
          is not registered". The backend cannot tell those apart — there is no
          domain-to-org mapping — and the common case is a new hire at a company
          that IS a customer but has not invited them yet. Both remedies are
          offered because either may apply.
        */
        <div className="auth-v2-notfound stack" role="status">
          <div className="auth-v2-notfound-icon" aria-hidden>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.9" />
              <path
                d="M12 7.6v5.2"
                stroke="currentColor"
                strokeWidth="1.9"
                strokeLinecap="round"
              />
              <circle cx="12" cy="16.4" r="1.1" fill="currentColor" />
            </svg>
          </div>
          <div>
            <p className="auth-v2-notfound-title">
              No account for <strong>{sentTo}</strong>
            </p>
            <p className="muted">{result.message}</p>
          </div>
          <div className="auth-v2-notfound-actions">
            <Link href="/signup" className="button">
              Set up your company
            </Link>
            <button type="button" className="button button-secondary" onClick={reset}>
              Try another email
            </button>
          </div>
        </div>
      ) : (
        <form className="stack" onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="email">Work email</label>
            <input
              id="email"
              className="input"
              type="email"
              required
              autoComplete="email"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          {error && (
            <p className="muted" role="alert" style={{ color: "var(--provenance-none)" }}>
              {error}
            </p>
          )}
          <button className="button auth-v2-submit" type="submit" disabled={loading}>
            {loading ? "Sending…" : "Send sign-in link"}
          </button>
        </form>
      )}
    </AuthShell>
  );
}
