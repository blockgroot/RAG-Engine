"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";

type InstallChoice = {
  id: string;
  login: string;
  account_type: string;
  available: boolean;
  unavailable_reason: string | null;
};

type PendingDetail = {
  scope: "org" | "workspace";
  workspace_id: string | null;
  installations: InstallChoice[];
  hint: string;
  install_another_url: string;
  switch_account_url: string;
};

/**
 * After GitHub user OAuth, pick which App installation binds to this Folio
 * surface (Company Sources vs a space). Auto-pick used to link the same
 * personal account on both — this page is the explicit separation step.
 */
function ChooseGitHubInstallInner() {
  const searchParams = useSearchParams();
  const pending = searchParams.get("pending") || "";
  const [detail, setDetail] = useState<PendingDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (!pending) {
      setError("Missing GitHub selection token. Start Connect again from Sources or your space.");
      return;
    }
    let cancelled = false;
    api
      .githubInstallPending(pending)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not load GitHub accounts.");
      });
    return () => {
      cancelled = true;
    };
  }, [pending]);

  async function choose(installationId: string) {
    if (!pending || busyId) return;
    setBusyId(installationId);
    setError(null);
    try {
      const { redirect_to } = await api.chooseGitHubInstall(pending, installationId);
      window.location.href = redirect_to;
    } catch (err) {
      setBusyId(null);
      setError(err instanceof ApiError ? err.message : "Could not finish connecting GitHub.");
    }
  }

  const title =
    detail?.scope === "workspace"
      ? "Choose GitHub for this space"
      : "Choose GitHub for Company Sources";

  const installLabel =
    detail?.scope === "workspace"
      ? "Connect a different account on GitHub"
      : "Install on a company Organization on GitHub";

  return (
    <main className="page" style={{ maxWidth: "36rem", margin: "0 auto", padding: "2.5rem 1.25rem" }}>
      <p className="eyebrow" style={{ marginBottom: "0.35rem" }}>
        GitHub
      </p>
      <h1 style={{ fontSize: "1.65rem", margin: "0 0 0.5rem" }}>{title}</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        {detail?.hint ||
          "Company Sources and each space must use different GitHub accounts so code access stays separate."}
      </p>

      {error && (
        <div className="banner banner-warn" role="alert" style={{ marginTop: "1.25rem" }}>
          {error}
        </div>
      )}

      {!detail && !error && <p className="muted">Loading accounts…</p>}

      {detail && detail.installations.length === 0 && (
        <div className="banner banner-warn" style={{ marginTop: "1.25rem" }}>
          No GitHub App installations found for this login yet.
        </div>
      )}

      {detail && detail.installations.length > 0 && (
        <ul className="stack" style={{ listStyle: "none", padding: 0, marginTop: "1.5rem" }}>
          {detail.installations.map((inst) => (
            <li key={inst.id} className="card" style={{ padding: "1rem 1.1rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem", alignItems: "center" }}>
                <div>
                  <strong style={{ display: "block" }}>{inst.login || "Unknown"}</strong>
                  <span className="muted" style={{ fontSize: "0.85rem" }}>
                    {inst.account_type === "Organization"
                      ? "GitHub Organization — typical for company code"
                      : inst.account_type === "User"
                        ? "Personal GitHub account — typical for a space"
                        : inst.account_type || "GitHub account"}
                  </span>
                  {!inst.available && inst.unavailable_reason && (
                    <p className="muted" style={{ margin: "0.35rem 0 0", fontSize: "0.85rem" }}>
                      {inst.unavailable_reason}
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  className="button"
                  disabled={!inst.available || busyId !== null}
                  onClick={() => choose(inst.id)}
                >
                  {busyId === inst.id ? "Connecting…" : inst.available ? "Use this" : "Unavailable"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {detail?.install_another_url && (
        <div className="stack" style={{ marginTop: "1.5rem", gap: "0.75rem" }}>
          <a className="button" href={detail.install_another_url}>
            {installLabel}
          </a>
          <a className="button button-secondary" href={detail.switch_account_url}>
            Sign in to GitHub as someone else
          </a>
        </div>
      )}
    </main>
  );
}

export default function ChooseGitHubInstallPage() {
  return (
    <Suspense fallback={<main className="page"><p className="muted">Loading…</p></main>}>
      <ChooseGitHubInstallInner />
    </Suspense>
  );
}
