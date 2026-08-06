"use client";

import { useState } from "react";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [devLink, setDevLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { dev_link } = await api.requestMagicLink(email);
      setDevLink(dev_link);
      setSubmitted(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell
      footer={
        <p>
          First time here? <Link href="/signup">Set up your company</Link>
        </p>
      }
    >
      <div className="auth-v2-card-head">
        <p className="auth-v2-card-kicker">Welcome back</p>
        <h2>Sign in to Folio</h2>
        <p className="muted">
          Enter your work email and we&rsquo;ll send a one-time sign-in link. No
          password needed.
        </p>
      </div>

      {submitted ? (
        <div className="auth-v2-success stack">
          <div className="auth-v2-success-icon" aria-hidden>
            ✓
          </div>
          <p>
            If that email is eligible, we&rsquo;ve sent a sign-in link. Check your
            inbox — it expires shortly and works once.
          </p>
          {devLink && (
            <p className="muted">
              Dev mode: <a href={devLink}>Continue to sign in</a>
            </p>
          )}
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
