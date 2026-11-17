"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { API_BASE_URL } from "@/lib/api";
import { apiFetch, useConnecting } from "@/lib/serverWake";

/**
 * Magic-link landing. A real navigation (not `fetch`) to the API's verify
 * endpoint, so the browser stores the Set-Cookie. Via `/api` the cookie is
 * first-party; another site (e.g. onrender.com) loses it to SameSite=Lax.
 *
 * The link is often opened after the API has gone idle, and a navigation to a
 * sleeping API through the proxy lands on its host's bot challenge, not on the
 * sign-in. So the page wakes it and waits for /health first.
 */

function VerifyRedirect() {
  const params = useSearchParams();
  const token = params.get("token");
  const connecting = useConnecting();

  useEffect(() => {
    if (!token) return;
    const go = () =>
      window.location.replace(`${API_BASE_URL}/auth/magic-link/verify?token=${encodeURIComponent(token)}`);
    // Still not answering after the wait: try anyway, the verify page says what failed.
    apiFetch("/api/health", { cache: "no-store" }).then(go, go);
  }, [token]);

  if (!token) return <p>This link is missing a token.</p>;
  return (
    <>
      <p>{connecting ? "Connecting…" : "Signing you in…"}</p>
      {connecting && (
        <p className="muted">
          This is taking longer than usual. Please keep this page open; you will be signed in as soon
          as the connection completes.
        </p>
      )}
    </>
  );
}

export default function VerifyPage() {
  return (
    <main className="page">
      <div className="card">
        <Suspense fallback={<p>Loading…</p>}>
          <VerifyRedirect />
        </Suspense>
      </div>
    </main>
  );
}
