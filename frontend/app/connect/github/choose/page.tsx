"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { BrandGlyph } from "@/components/BrandGlyph";
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

/** After GitHub OAuth, pick which App installation binds here (company or a
 *  space), so one personal account is not linked to both. */
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

  const back = detail?.scope === "workspace" && detail.workspace_id ? `/workspaces/${detail.workspace_id}` : "/admin/connections";

  return (
    <main className="gh-choose">
      <section className="gh-choose-card">
        <span className="gh-choose-badge" aria-hidden>
          <BrandGlyph name="github" size={30} />
        </span>
        <p className="gh-choose-eyebrow">Connect GitHub</p>
        <h1>{title}</h1>
        <p className="gh-choose-hint">
          {detail?.hint ||
            "Company Sources and each space must use different GitHub accounts so code access stays separate."}
        </p>

        {error && (
          <div className="banner banner-warn" role="alert">
            {error}
          </div>
        )}

        {!detail && !error && (
          <ul className="gh-choose-list" aria-hidden>
            <li className="gh-choose-row is-loading" />
            <li className="gh-choose-row is-loading" />
          </ul>
        )}

        {detail && detail.installations.length === 0 && (
          <p className="gh-choose-empty">No GitHub accounts have Handbook installed yet. Install it below.</p>
        )}

        {detail && detail.installations.length > 0 && (
          <ul className="gh-choose-list">
            {detail.installations.map((inst, i) => {
              const org = inst.account_type === "Organization";
              return (
                <li
                  key={inst.id}
                  className="gh-choose-row"
                  data-available={inst.available}
                  style={{ ["--i" as string]: i }}
                >
                  <span className="gh-choose-avatar" aria-hidden>
                    {(inst.login || "?").charAt(0).toUpperCase()}
                  </span>
                  <span className="gh-choose-copy">
                    <strong>{inst.login || "Unknown"}</strong>
                    <span className={`gh-choose-type${org ? " is-org" : ""}`}>
                      {org ? "Organization · usual for company code" : inst.account_type === "User" ? "Personal · usual for a space" : inst.account_type || "GitHub account"}
                    </span>
                    {!inst.available && inst.unavailable_reason && (
                      <small className="gh-choose-why">{inst.unavailable_reason}</small>
                    )}
                  </span>
                  <button
                    type="button"
                    className="button"
                    disabled={!inst.available || busyId !== null}
                    onClick={() => choose(inst.id)}
                  >
                    {busyId === inst.id ? "Connecting…" : inst.available ? "Use this" : "In use"}
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        {detail?.install_another_url && (
          <div className="gh-choose-other">
            <p>Not listed?</p>
            <a className="button button-secondary" href={detail.install_another_url}>
              {installLabel}
            </a>
            <a className="gh-choose-link" href={detail.switch_account_url}>
              Sign in to GitHub as someone else
            </a>
          </div>
        )}

        <a className="gh-choose-back" href={back}>
          ← Back
        </a>
      </section>
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
