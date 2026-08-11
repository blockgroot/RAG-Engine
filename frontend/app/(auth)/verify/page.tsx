"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { API_BASE_URL } from "@/lib/api";

/**
 * The emailed magic link points here (not directly at the API) so the token
 * lives in a frontend-owned URL first. This page's only job is a real browser
 * navigation (not a `fetch`) to the API's verify endpoint, so the browser
 * actually processes the Set-Cookie header and follows the redirect home —
 * a `fetch` would see the cookie in JS metadata but never store it.
 *
 * With `NEXT_PUBLIC_API_BASE_URL=/api` this navigation stays on the frontend
 * origin (`/api/auth/magic-link/verify` → FastAPI via next.config rewrite),
 * so the cookie is first-party. Do not point this at a different site
 * (e.g. onrender.com) or SameSite=Lax will drop it on later `/me` fetches.
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
