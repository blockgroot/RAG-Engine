"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { clearMeCache, useMe } from "@/lib/useMe";
import { ApiError, LinkedIdentities, LinkedIdentity, api } from "@/lib/api";
import { ActPanel } from "@/components/ActPanel";
import { MemoryPanel } from "@/components/MemoryPanel";
import { SignInEmailPanel } from "@/components/SignInEmailPanel";
import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { spaceStyle } from "@/components/SpaceMark";

const TOOL_NAMES: Record<string, string> = {
  github: "GitHub",
  slack: "Slack",
  google: "Google Drive",
  notion: "Notion",
  linear: "Linear",
};

const TOOL_BRANDS: Record<string, BrandName> = {
  github: "github",
  slack: "slack",
  google: "drive",
  notion: "notion",
  linear: "linear",
};

function toolName(provider: string) {
  return TOOL_NAMES[provider] ?? provider;
}

function accountLabel(identity: LinkedIdentity) {
  if (identity.provider === "github") return `@${identity.account}`;
  return (
    identity.display_name ||
    identity.email ||
    identity.account.replace(/^email:/, "")
  );
}

/**
 * Linked accounts: which accounts in your company's tools are you.
 * Matched by email; GitHub (usernames only) is linked by hand. A link changes
 * credit for work, never what anyone can read.
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
  const hasGitHub =
    data?.identities.some((i) => i.provider === "github") ?? false;

  return (
    <AppShell me={me}>
      <main className="page-wide studio-page stack account-page">
        <PageHeader
          eyebrow="You"
          title="Your account"
          description="Your name, the accounts in each tool that are yours, and your settings."
        />

        {linked && (
          <div className="banner banner-ok" role="status">
            Your GitHub account is linked.
          </div>
        )}
        {linkFailed && (
          <div className="banner banner-warn" role="alert">
            GitHub didn’t confirm your account, so nothing was linked. You can
            try again.
          </div>
        )}
        {error && (
          <div className="banner banner-warn" role="alert">
            {error}
          </div>
        )}

        <section
          className="studio-panel account-profile"
          aria-labelledby="name-title"
        >
          <span
            className="account-avatar"
            style={spaceStyle(me.email)}
            aria-hidden
          >
            {(me.name || me.email || "?").trim().charAt(0).toUpperCase()}
          </span>
          <div className="account-profile-body">
            <h2 id="name-title" className="sr-only">
              Your name
            </h2>
            <YourName current={me.name} />
            <p className="account-profile-meta">
              <span>{me.email}</span>
              <span className={`role-chip role-${me.role}`}>
                {me.role === "admin" ? "Admin" : "Member"}
              </span>
              {me.org_name && <span>{me.org_name}</span>}
            </p>
          </div>
        </section>

        <section
          className="studio-panel stack account-identities"
          aria-labelledby="linked-title"
        >
          <div className="account-identities-head">
            <div className="studio-section-head">
              <h2 id="linked-title">Your accounts</h2>
              <p className="muted">
                Handbook uses these to know which messages, documents and pull
                requests are yours. Linking never changes what anyone can see.
              </p>
            </div>
            {data?.github_link_available && (
              <a
                className="button button-secondary button-sm account-github-link"
                href={api.githubLinkUrl()}
              >
                {hasGitHub ? "Link another GitHub" : "Link GitHub"}
              </a>
            )}
          </div>

          {!data ? (
            <p className="muted">Loading…</p>
          ) : data.identities.length === 0 ? (
            <p className="muted">
              Nothing is linked yet. If your tools use a different email from
              the one you sign in with, they won’t link on their own.
            </p>
          ) : (
            <ul className="account-identity-list">
              {data.identities.map((identity) => (
                <li
                  key={identity.id}
                  className={`account-identity-row is-${identity.provider}`}
                >
                  <span className="account-identity-mark" aria-hidden="true">
                    <BrandGlyph
                      name={TOOL_BRANDS[identity.provider] ?? "document"}
                      size={26}
                    />
                  </span>
                  <div className="account-identity-copy">
                    <strong>{toolName(identity.provider)}</strong>
                    <span>{accountLabel(identity)}</span>
                  </div>
                  <div className="account-identity-foot">
                    <span
                      className="account-identity-method"
                      data-oauth={identity.verified_by === "oauth"}
                    >
                      {identity.verified_by === "oauth"
                        ? "Signed in by you"
                        : "Matched by email"}
                    </span>
                    {identity.can_unlink && (
                      <button
                        type="button"
                        className="account-identity-unlink"
                        disabled={busy === identity.id}
                        onClick={() => unlink(identity)}
                      >
                        {busy === identity.id ? "Unlinking…" : "Unlink"}
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {me.act_enabled && <ActPanel />}

        <div className="account-settings-grid">
          <SignInEmailPanel />
          <MemoryPanel />
        </div>
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

function YourName({ current }: { current: string | null }) {
  const [name, setName] = useState(current ?? "");
  const [saved, setSaved] = useState(current ?? "");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  return (
    <div className="account-name">
      <form
        className="account-name-form"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setNote(null);
          api
            .setName(name)
            .then((r) => {
              setName(r.name);
              setSaved(r.name);
              setNote("Saved.");
              clearMeCache();
            })
            .catch((err) =>
              setNote(
                err instanceof ApiError
                  ? err.message
                  : "Could not save. Try again.",
              ),
            )
            .finally(() => setBusy(false));
        }}
      >
        <input
          className="input"
          aria-label="Your name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={60}
          autoComplete="name"
        />
        <button
          className="button button-secondary"
          type="submit"
          disabled={busy || !name.trim() || name.trim() === saved}
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </form>
      {note && (
        <p className="muted account-name-note" role="status">
          {note}
        </p>
      )}
    </div>
  );
}
