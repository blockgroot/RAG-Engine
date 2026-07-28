"use client";

import { useState } from "react";
import Link from "next/link";
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
    <main className="page">
      <div className="stack">
        <div>
          <p className="eyebrow">Policy Portal</p>
          <h1>Sign in</h1>
          <p className="muted">Use your work email — we&rsquo;ll send a one-time link. No password.</p>
        </div>

        {submitted ? (
          <div className="card stack">
            <p>If that email is eligible, we&rsquo;ve sent a sign-in link. Check your inbox &mdash; it expires shortly and works once.</p>
            {devLink && (
              <p className="muted">
                Dev mode (no email sender configured):{" "}
                <a href={devLink}>Continue to sign in</a>
              </p>
            )}
          </div>
        ) : (
          <form className="card stack" onSubmit={handleSubmit}>
            <div className="field">
              <label htmlFor="email">Work email</label>
              <input
                id="email"
                className="input"
                type="email"
                required
                placeholder="you@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            {error && <p className="muted" style={{ color: "var(--provenance-none)" }}>{error}</p>}
            <button className="button" type="submit" disabled={loading}>
              {loading ? "Sending…" : "Send sign-in link"}
            </button>
          </form>
        )}

        <p className="muted">
          First time here? <Link href="/signup">Set up your company</Link>
        </p>
      </div>
    </main>
  );
}
