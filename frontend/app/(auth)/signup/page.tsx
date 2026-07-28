"use client";

import { useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [devLink, setDevLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const { dev_link } = await api.signup(email, companyName);
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
          <p className="eyebrow">Get started</p>
          <h1>Create your organization</h1>
          <p className="muted">
            You&rsquo;ll be the admin. Next you&rsquo;ll connect Notion, sync policies, then invite your team.
          </p>
        </div>

        {submitted ? (
          <div className="card stack">
            <p>Check your inbox for a sign-in link to finish setting up {companyName}.</p>
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
            {error && <p className="muted" style={{ color: "var(--provenance-none)" }}>{error}</p>}
            <button className="button" type="submit" disabled={loading}>
              {loading ? "Creating…" : "Create organization"}
            </button>
          </form>
        )}

        <p className="muted">
          Already have an account? <Link href="/login">Sign in</Link>
        </p>
      </div>
    </main>
  );
}
