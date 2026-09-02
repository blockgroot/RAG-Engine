"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { BrandMark } from "@/components/BrandMark";
import { api, Me } from "@/lib/api";
import { canAccessAdminPortal, isSetupComplete } from "@/lib/routing";
import { clearMeCache } from "@/lib/useMe";

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

function IconCharts() {
  // Axes with two bars and a rising line: the section is measurement over
  // time, not a document. Monochrome `currentColor` like every other rail icon.
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M4 4v16h16" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
      <rect x="7.5" y="12" width="3" height="5" rx="1" stroke="currentColor" strokeWidth="1.6" />
      <rect x="13" y="8.5" width="3" height="8.5" rx="1" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function IconReports() {
  // Calendar + clock: what the section is (something that runs on a cadence),
  // not a generic document. Monochrome `currentColor` like every other rail
  // icon — the colour version of this mark lives in BrandGlyph for the
  // marketing pages.
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="3" y="5" width="14" height="14" rx="2" stroke="currentColor" strokeWidth="1.75" />
      <path d="M3 9h14M7.5 3v3.5M12.5 3v3.5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
      <circle cx="17.5" cy="16.5" r="4" fill="var(--surface, #fff)" stroke="currentColor" strokeWidth="1.6" />
      <path d="M17.5 14.7v1.9h1.4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
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

function IconModel() {
  // A chip: the model itself. Sized and stroked exactly like IconSources and
  // IconPeople (18px, 24-unit viewBox, 1.75 stroke) so the rail reads as one
  // set — and geometric rather than pictorial, like its neighbours.
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="7" y="7" width="10" height="10" rx="2.5" stroke="currentColor" strokeWidth="1.75" />
      <path
        d="M10 4v3M14 4v3M10 17v3M14 17v3M4 10h3M4 14h3M17 10h3M17 14h3"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
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
    clearMeCache();
    router.replace("/login");
    router.refresh();
  }

  const setupDone = me ? isSetupComplete(me) : false;
  const showAdmin = me ? canAccessAdminPortal(me) : false;
  const homeHref = setupDone || me?.role === "member" ? "/chat" : "/onboarding";
  const showMainNav = Boolean(me && variant !== "onboarding");
  const firstName = (() => {
    const local = me?.email?.split("@")[0] || "";
    const name = local.split(/[.\-_]/)[0];
    return name ? name.charAt(0).toUpperCase() + name.slice(1) : null;
  })();
  const initial = (firstName || me?.org_name || "F").trim().charAt(0).toUpperCase();

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
                  <span className="rail-link-hint">Company-wide</span>
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
                <span className="rail-link-hint">Private, invite-only</span>
              </span>
            </Link>
            <Link
              href="/visualizations"
              className="rail-link"
              data-active={pathname.startsWith("/visualizations") ? "true" : "false"}
              aria-current={pathname.startsWith("/visualizations") ? "page" : undefined}
            >
              <span className="rail-ico">
                <IconCharts />
              </span>
              <span className="rail-link-copy">
                <span className="rail-link-title">Visualizations</span>
                <span className="rail-link-hint">Counted from your data</span>
              </span>
            </Link>
            <Link
              href="/schedulers"
              className="rail-link"
              data-active={pathname.startsWith("/schedulers") ? "true" : "false"}
              aria-current={pathname.startsWith("/schedulers") ? "page" : undefined}
            >
              <span className="rail-ico">
                <IconReports />
              </span>
              <span className="rail-link-copy">
                <span className="rail-link-title">Reports</span>
                <span className="rail-link-hint">Emailed on a schedule</span>
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
                  href="/admin/model"
                  className="rail-link"
                  data-active={pathname.startsWith("/admin/model") ? "true" : "false"}
                  aria-current={pathname.startsWith("/admin/model") ? "page" : undefined}
                >
                  <span className="rail-ico">
                    <IconModel />
                  </span>
                  <span className="rail-link-copy">
                    <span className="rail-link-title">Model</span>
                    <span className="rail-link-hint">Use your own key</span>
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
                <span className="user-name-row">
                  {firstName && <span className="user-name-chip">{firstName}</span>}
                  <span className={`role-chip role-${me.role}`}>
                    {me.role === "admin" ? "Admin" : "Member"}
                  </span>
                </span>
                {me.org_name && <span className="org-chip">{me.org_name}</span>}
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
              label="Bring in documents"
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
