"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { API_BASE_URL } from "@/lib/api";

/**
 * Magic-link landing. A real navigation (not `fetch`) to the API's verify
 * endpoint, so the browser stores the Set-Cookie. Via `/api` the cookie is
 * first-party; another site (e.g. onrender.com) loses it to SameSite=Lax.
 */
function VerifyRedirect() {
  const params = useSearchParams();
  const token = params.get("token");

  useEffect(() => {
    if (token) {
      window.location.replace(`${API_BASE_URL}/auth/magic-link/verify?token=${encodeURIComponent(token)}`);
    }
  }, [token]);

  return <p>{token ? "Signing you in…" : "This link is missing a token."}</p>;
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
