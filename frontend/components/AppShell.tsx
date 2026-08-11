"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { BrandMark } from "@/components/BrandMark";
import { api, Me } from "@/lib/api";
import { canAccessAdminPortal, isSetupComplete } from "@/lib/routing";

type Variant = "app" | "onboarding" | "admin";

function IconAsk() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="1.75" />
      <path d="M16.5 16.5 21 21" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
    </svg>
  );
}

function IconSpaces() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="3" y="3" width="8" height="8" rx="2" stroke="currentColor" strokeWidth="1.75" />
      <rect x="13" y="3" width="8" height="8" rx="2" stroke="currentColor" strokeWidth="1.75" />
      <rect x="3" y="13" width="8" height="8" rx="2" stroke="currentColor" strokeWidth="1.75" />
      <rect x="13" y="13" width="8" height="8" rx="2" stroke="currentColor" strokeWidth="1.75" />
    </svg>
  );
}

function IconSources() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M4 7.5C4 6.12 7.58 5 12 5s8 1.12 8 2.5S16.42 10 12 10 4 8.88 4 7.5Z"
        stroke="currentColor"
        strokeWidth="1.75"
      />
      <path
        d="M4 7.5V12c0 1.38 3.58 2.5 8 2.5s8-1.12 8-2.5V7.5"
        stroke="currentColor"
        strokeWidth="1.75"
      />
      <path
        d="M4 12v4.5c0 1.38 3.58 2.5 8 2.5s8-1.12 8-2.5V12"
        stroke="currentColor"
        strokeWidth="1.75"
      />
    </svg>
  );
}

function IconPeople() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="9" cy="8" r="3.25" stroke="currentColor" strokeWidth="1.75" />
      <path
        d="M3.5 19c.6-3.1 2.9-5 5.5-5s4.9 1.9 5.5 5"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
      <circle cx="17" cy="9" r="2.5" stroke="currentColor" strokeWidth="1.75" />
      <path
        d="M15.2 19c.35-1.7 1.4-3 2.8-3 1.2 0 2.15.85 2.7 2.2"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function AppShell({
  me,
  variant = "app",
  children,
}: {
  me: Me | null;
  variant?: Variant;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  async function handleSignOut() {
    try {
      await api.logout();
    } catch {
      /* still clear local route */
    }
    router.replace("/login");
    router.refresh();
  }

  const setupDone = me ? isSetupComplete(me) : false;
  const showAdmin = me ? canAccessAdminPortal(me) : false;
  const homeHref = setupDone || me?.role === "member" ? "/chat" : "/onboarding";
  const showMainNav = Boolean(me && variant !== "onboarding");
  const initial = (me?.org_name || "F").trim().charAt(0).toUpperCase();

  return (
    <div className={`app-shell ${showMainNav ? "app-shell-nav" : "app-shell-simple"}`}>
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>

      <aside className="app-rail" aria-label="Primary">
        <div className="rail-atmosphere" aria-hidden>
          <span className="rail-glow rail-glow-a" />
          <span className="rail-glow rail-glow-b" />
        </div>
        <Link href={homeHref} className="brand">
          <BrandMark />
          <span className="brand-text">
            <span className="brand-name">Handbook</span>
            <span className="brand-tag">Work answers, grounded</span>
          </span>
        </Link>

        {showMainNav && (
          <nav className="rail-nav" aria-label="Primary">
            <p className="rail-label" id="nav-explore">
              Explore
            </p>
            {(setupDone || me?.role === "member") && (
              <Link
                href="/chat"
                className="rail-link"
                data-active={pathname.startsWith("/chat") ? "true" : "false"}
                aria-current={pathname.startsWith("/chat") ? "page" : undefined}
              >
                <span className="rail-ico">
                  <IconAsk />
                </span>
                <span className="rail-link-copy">
                  <span className="rail-link-title">Ask</span>
                  <span className="rail-link-hint">Ask anything</span>
                </span>
              </Link>
            )}
            <Link
              href="/workspaces"
              className="rail-link"
              data-active={pathname.startsWith("/workspaces") ? "true" : "false"}
              aria-current={pathname.startsWith("/workspaces") ? "page" : undefined}
            >
              <span className="rail-ico">
                <IconSpaces />
              </span>
              <span className="rail-link-copy">
                <span className="rail-link-title">Spaces</span>
                <span className="rail-link-hint">Private rooms</span>
              </span>
            </Link>

            {showAdmin && (
              <>
                <p className="rail-label" id="nav-company">
                  Company
                </p>
                <Link
                  href="/admin/connections"
                  className="rail-link"
                  data-active={pathname.startsWith("/admin/connections") ? "true" : "false"}
                  aria-current={
                    pathname.startsWith("/admin/connections") ? "page" : undefined
                  }
                >
                  <span className="rail-ico">
                    <IconSources />
                  </span>
                  <span className="rail-link-copy">
                    <span className="rail-link-title">Sources</span>
                    <span className="rail-link-hint">Connect apps</span>
                  </span>
                </Link>
                <Link
                  href="/admin/members"
                  className="rail-link"
                  data-active={pathname.startsWith("/admin/members") ? "true" : "false"}
                  aria-current={pathname.startsWith("/admin/members") ? "page" : undefined}
                >
                  <span className="rail-ico">
                    <IconPeople />
                  </span>
                  <span className="rail-link-copy">
                    <span className="rail-link-title">People</span>
                    <span className="rail-link-hint">Grow the team</span>
                  </span>
                </Link>
              </>
            )}
          </nav>
        )}

        {me && (
          <div className="rail-foot">
            <div className="rail-user" aria-label="Signed-in account">
              <span className="rail-avatar" aria-hidden>
                {initial}
              </span>
              <div className="rail-user-copy">
                {me.org_name && <span className="org-chip">{me.org_name}</span>}
                <span className={`role-chip role-${me.role}`}>
                  {me.role === "admin" ? "Admin" : "Member"}
                </span>
                <button type="button" className="rail-sign-out" onClick={handleSignOut}>
                  Sign out
                </button>
              </div>
            </div>
          </div>
        )}
      </aside>

      <div className="app-main">
        {variant === "onboarding" && me && (
          <div className="onboarding-progress" aria-label="Setup progress">
            <Step n={1} label="Connect" done={me.has_connection} active={!me.has_connection} />
            <Step
              n={2}
              label="Bring in policies"
              done={me.ready_to_ask}
              active={me.has_connection && !me.ready_to_ask}
            />
            <Step
              n={3}
              label="Invite people"
              done={false}
              active={me.has_connection && me.ready_to_ask}
            />
          </div>
        )}
        <div
          id="main-content"
          className={variant === "admin" ? "admin-body" : "app-body"}
          tabIndex={-1}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

function Step({
  n,
  label,
  done,
  active,
}: {
  n: number;
  label: string;
  done: boolean;
  active: boolean;
}) {
  return (
    <div className="onboarding-step" data-done={done} data-active={active}>
      <span className="onboarding-step-n" aria-hidden>
        {done ? "✓" : n}
      </span>
      <span>{label}</span>
    </div>
  );
}
