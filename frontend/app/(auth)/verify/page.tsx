"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { API_BASE_URL } from "@/lib/api";

/**
 * The emailed magic link points here (not directly at the API) so the token
 * lives in a frontend-owned URL first. This page's only job is a real browser
 * navigation (not a `fetch`) to the API's verify endpoint, so the browser
 * actually processes the API's Set-Cookie response header and follows its
 * redirect back to /chat — a `fetch` here would receive the cookie in JS
 * response metadata but never store it against the API's origin the way a
 * top-level navigation does.
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
