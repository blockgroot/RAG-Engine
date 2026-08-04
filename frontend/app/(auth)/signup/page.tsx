"use client";

import { useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.signup(email, companyName);
      setSubmitted(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-stage">
      <div className="auth-panel">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden />
          <span className="brand" style={{ fontSize: "1.2rem" }}>
            Policy Portal
          </span>
        </div>
        <p className="eyebrow">Get started</p>
        <h1>Request your organization</h1>
        <p className="muted">
          Requests are reviewed before an organization is created. Once approved,
          you&rsquo;ll be the admin — next: connect Notion, sync policies, invite your team.
        </p>

        {submitted ? (
          <div className="card stack">
            <p>
              Thanks! Your request to create {companyName} has been received. We&rsquo;ll
              email you once it&rsquo;s approved.
            </p>
          </div>
        ) : (
          <form className="card stack" onSubmit={handleSubmit}>
            <div className="field">
              <label htmlFor="companyName">Company name</label>
              <input
                id="companyName"
                className="input"
                required
                placeholder="Acme Inc."
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="email">Your work email</label>
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
            {error && (
              <p className="muted" style={{ color: "var(--provenance-none)" }}>
                {error}
              </p>
            )}
            <button className="button" type="submit" disabled={loading}>
              {loading ? "Submitting…" : "Request access"}
            </button>
          </form>
        )}

        <p className="muted" style={{ marginTop: "1.25rem" }}>
          Already have an account? <Link href="/login">Sign in</Link>
        </p>
      </div>
    </main>
  );
}
