"use client";

import { useState } from "react";
import Link from "next/link";
import { AuthShell } from "@/components/AuthShell";
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
    <AuthShell
      footer={
        <p>
          Already have an account? <Link href="/login">Sign in</Link>
        </p>
      }
    >
      <div className="auth-v2-card-head">
        <p className="auth-v2-card-kicker">Get started</p>
        <h2>Request your organization</h2>
        <p className="muted">
          Requests are reviewed before an organization is created. Once approved,
          you&rsquo;ll be the admin — connect sources, sync policies, invite your
          team.
        </p>
      </div>

      {submitted ? (
        <div className="auth-v2-success stack">
          <div className="auth-v2-success-icon" aria-hidden>
            ✓
          </div>
          <p>
            Thanks! Your request to create <strong>{companyName}</strong> has been
            received. We&rsquo;ll email you once it&rsquo;s approved.
          </p>
        </div>
      ) : (
        <form className="stack" onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="companyName">Company name</label>
            <input
              id="companyName"
              className="input"
              required
              autoComplete="organization"
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
            {loading ? "Submitting…" : "Request access"}
          </button>
        </form>
      )}
    </AuthShell>
  );
}
