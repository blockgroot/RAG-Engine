"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.requestMagicLink(email);
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
          <h1>Policy Portal</h1>
          <p className="muted">Sign in with your work email to ask questions grounded in your company&rsquo;s policies.</p>
        </div>

        {submitted ? (
          <div className="card">
            <p>If that email is eligible, we&rsquo;ve sent a sign-in link. Check your inbox &mdash; it expires shortly and works once.</p>
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
      </div>
    </main>
  );
}
