"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { LinkedIdentities, LinkedIdentity, api } from "@/lib/api";

const TOOL_NAMES: Record<string, string> = {
  github: "GitHub",
  slack: "Slack",
  google: "Google Drive",
  notion: "Notion",
  linear: "Linear",
};

function toolName(provider: string) {
  return TOOL_NAMES[provider] ?? provider;
}

function accountLabel(identity: LinkedIdentity) {
  if (identity.provider === "github") return `@${identity.account}`;
  return identity.display_name || identity.email || identity.account.replace(/^email:/, "");
}

/**
 * Linked accounts: which accounts in your company's tools are you.
 *
 * Handbook links most of them by itself, when a tool reports the same email
 * you sign in with. GitHub gives out usernames, not emails, so that one you
 * link yourself. A link only changes who Handbook credits for work — it never
 * changes what you or anyone else can read.
 */
function AccountContent() {
  const { me, loading } = useMe();
  const params = useSearchParams();
  const [data, setData] = useState<LinkedIdentities | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api
      .linkedIdentities()
      .then(setData)
      .catch(() => setError("Could not load your linked accounts."));
  }, []);

  useEffect(() => {
    if (me) load();
  }, [me, load]);

  async function unlink(identity: LinkedIdentity) {
    setBusy(identity.id);
    try {
      await api.unlinkIdentity(identity.id);
      load();
    } catch {
      setError("Could not unlink that account. Try again.");
    } finally {
      setBusy(null);
    }
  }

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  const linked = params.get("linked") === "github";
  const linkFailed = params.get("link_error") === "github";
  const hasGitHub = data?.identities.some((i) => i.provider === "github") ?? false;

  return (
    <AppShell me={me}>
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="You"
          title="Linked accounts"
          description="Your accounts in the tools your company connected. Handbook uses these to know which Slack messages, documents and pull requests are yours. Linking never changes what you or anyone else can see."
          scene="people"
        />

        {linked && (
          <div className="banner banner-ok" role="status">
            Your GitHub account is linked.
          </div>
        )}
        {linkFailed && (
          <div className="banner banner-warn" role="alert">
            GitHub didn’t confirm your account, so nothing was linked. You can try again.
          </div>
        )}
        {error && (
          <div className="banner banner-warn" role="alert">
            {error}
          </div>
        )}

        <section className="studio-panel stack" aria-labelledby="linked-title">
          <div className="studio-section-head">
            <h2 id="linked-title">Your accounts</h2>
            <p className="muted">
              Slack, Google Drive, Notion and Linear link on their own when they report the
              email you sign in with. They show up here after your company’s next sync.
            </p>
          </div>

          {!data ? (
            <p className="muted">Loading…</p>
          ) : data.identities.length === 0 ? (
            <p className="muted">
              Nothing is linked yet. If your tools use a different email from the one you sign
              in with, they won’t link on their own.
            </p>
          ) : (
            <table className="gap-table">
              <thead>
                <tr>
                  <th>Tool</th>
                  <th>Account</th>
                  <th>Linked by</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {data.identities.map((identity) => (
                  <tr key={identity.id}>
                    <td>{toolName(identity.provider)}</td>
                    <td>{accountLabel(identity)}</td>
                    <td className="muted">
                      {identity.verified_by === "oauth" ? "You signed in" : "Matching email"}
                    </td>
                    <td>
                      {identity.can_unlink && (
                        <button
                          type="button"
                          className="button button-secondary button-sm"
                          disabled={busy === identity.id}
                          onClick={() => unlink(identity)}
                        >
                          {busy === identity.id ? "Unlinking…" : "Unlink"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        {data?.github_link_available && (
          <section className="studio-panel stack" aria-labelledby="github-title">
            <div className="studio-section-head">
              <h2 id="github-title">GitHub</h2>
              <p className="muted">
                GitHub gives out usernames rather than emails, so Handbook can’t match it on its
                own. Sign in to GitHub once to show which account is yours. Handbook reads your
                username and keeps no access to your account.
              </p>
            </div>
            <div>
              <a className="button" href={api.githubLinkUrl()}>
                {hasGitHub ? "Link another GitHub account" : "Link your GitHub account"}
              </a>
            </div>
          </section>
        )}
      </main>
    </AppShell>
  );
}

export default function AccountPage() {
  return (
    <Suspense
      fallback={
        <main className="page">
          <p className="muted">Loading…</p>
        </main>
      }
    >
      <AccountContent />
    </Suspense>
  );
}
