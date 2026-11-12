"use client";

import { NewVersionPill } from "./NewVersionPill";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { BrandMark } from "@/components/BrandMark";
import { NamePrompt } from "@/components/NamePrompt";
import { NotificationBell } from "@/components/NotificationBell";
import { RailChats } from "@/components/RailChats";
import { api, Me } from "@/lib/api";
import { canAccessAdminPortal, isSetupComplete } from "@/lib/routing";
import { clearMeCache } from "@/lib/useMe";

type Variant = "app" | "onboarding" | "admin";

/* Rail icons: one set. 24-unit box, 1.75 stroke, and a soft fill of the
   same colour so each reads as a shape, not a hairline, in the dark rail. */
const SOFT = { fill: "currentColor", fillOpacity: 0.24 } as const;
const LINE = { stroke: "currentColor", strokeWidth: 1.75, strokeLinecap: "round", strokeLinejoin: "round" } as const;

function RailIcon({ children }: { children: React.ReactNode }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
      {children}
    </svg>
  );
}

function IconAsk() {
  // A speech bubble with a spark: ask, and get an answer.
  return (
    <RailIcon>
      <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5H11l-4.5 3.5V17h0A2.5 2.5 0 0 1 4 14.5v-8Z" {...SOFT} {...LINE} />
      <path d="M12 7.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9.9-2.1Z" fill="currentColor" />
    </RailIcon>
  );
}

function IconSpaces() {
  // Rooms: four tiles, one lit.
  return (
    <RailIcon>
      <rect x="3.5" y="3.5" width="7.5" height="7.5" rx="2" {...SOFT} {...LINE} />
      <rect x="13" y="3.5" width="7.5" height="7.5" rx="2" {...LINE} />
      <rect x="3.5" y="13" width="7.5" height="7.5" rx="2" {...LINE} />
      <rect x="13" y="13" width="7.5" height="7.5" rx="2" {...LINE} />
    </RailIcon>
  );
}

function IconReports() {
  // A page with bars: a report that arrives on a schedule.
  return (
    <RailIcon>
      <path d="M6.5 3.5h7l4.5 4.5v10a2.5 2.5 0 0 1-2.5 2.5h-9A2.5 2.5 0 0 1 4 18V6a2.5 2.5 0 0 1 2.5-2.5Z" {...SOFT} {...LINE} />
      <path d="M13.5 3.5V8H18" {...LINE} />
      <path d="M8 17v-3M11 17v-5M14 17v-2" {...LINE} />
    </RailIcon>
  );
}

function IconAct() {
  // A bolt: Handbook doing something in a tool, not only reading it.
  return (
    <RailIcon>
      <path d="M13 2.75 4.75 13.5H11l-1 7.75 8.25-10.75H12l1-7.75Z" {...SOFT} {...LINE} />
    </RailIcon>
  );
}

function IconSources() {
  // Plugged-in apps: two linked blocks.
  return (
    <RailIcon>
      <rect x="3.5" y="3.5" width="9" height="9" rx="2.5" {...SOFT} {...LINE} />
      <rect x="11.5" y="11.5" width="9" height="9" rx="2.5" {...LINE} />
      <path d="M15 8h2.5A1.5 1.5 0 0 1 19 9.5V11M9 16H6.5A1.5 1.5 0 0 1 5 14.5V13" {...LINE} />
    </RailIcon>
  );
}

function IconModel() {
  // Sparkles: the AI model that writes the answers.
  return (
    <RailIcon>
      <path d="M10 3.5l1.6 4.4 4.4 1.6-4.4 1.6L10 15.5l-1.6-4.4L4 9.5l4.4-1.6L10 3.5Z" {...SOFT} {...LINE} />
      <path d="M17.5 14l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2Z" fill="currentColor" />
    </RailIcon>
  );
}

function IconUpdates() {
  // An arrow rising out of a circle: a newer version.
  return (
    <RailIcon>
      <circle cx="12" cy="12" r="8.5" {...SOFT} {...LINE} />
      <path d="M12 16.5v-9M8.5 11 12 7.5l3.5 3.5" {...LINE} />
    </RailIcon>
  );
}

function IconPeople() {
  return (
    <RailIcon>
      <circle cx="9" cy="8" r="3.25" {...SOFT} {...LINE} />
      <path d="M3.5 19c.6-3.1 2.9-5 5.5-5s4.9 1.9 5.5 5" {...LINE} />
      <circle cx="17" cy="9" r="2.5" {...LINE} />
      <path d="M15.2 15.6c.8-.4 1.6-.6 2.3-.6 1.7 0 3 1.4 3.5 3.6" {...LINE} />
    </RailIcon>
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
  // Phones: the sidebar is a drawer behind a menu button (the ChatGPT and
  // Claude pattern) instead of a stack of navigation above every page.
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);
  useEffect(() => {
    if (!navOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setNavOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navOpen]);
  const [confirmOut, setConfirmOut] = useState(false);
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
  // Saved in this page view before /me is fetched again.
  const [savedName, setSavedName] = useState<string | null>(null);
  const guessedName = (() => {
    const local = me?.email?.split("@")[0] || "";
    const name = local.split(/[.\-_]/)[0];
    return name ? name.charAt(0).toUpperCase() + name.slice(1) : null;
  })();
  const chosenName = savedName ?? me?.name ?? null;
  // Shown as the person wrote it; the guess only until they have said.
  const firstName = chosenName ?? guessedName;
  const initial = (firstName || me?.org_name || "F").trim().charAt(0).toUpperCase();

  return (
    <div
      className={`app-shell ${showMainNav ? "app-shell-nav" : "app-shell-simple"}${
        navOpen ? " rail-open" : ""
      }`}
    >
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>

      {showMainNav && (
        <header className="mobile-bar">
          <button
            type="button"
            className="mobile-bar-menu"
            onClick={() => setNavOpen(true)}
            aria-label="Open menu"
            aria-expanded={navOpen}
            aria-controls="app-rail"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
            </svg>
          </button>
          <Link href={homeHref} className="mobile-bar-brand">
            <BrandMark />
            <span>Handbook</span>
          </Link>
        </header>
      )}
      {navOpen && <div className="rail-backdrop" onClick={() => setNavOpen(false)} aria-hidden />}

      <aside
        className="app-rail"
        id="app-rail"
        aria-label="Primary"
        onClick={(e) => {
          // Picking a destination closes the drawer, including a chat in the
          // history list that only changes the query string.
          if ((e.target as HTMLElement).closest("a")) setNavOpen(false);
        }}
      >
        <div className="rail-atmosphere" aria-hidden>
          <span className="rail-glow rail-glow-a" />
          <span className="rail-glow rail-glow-b" />
        </div>
        {/* The bell rides the BRAND row, not the foot. Two reasons: a status
            indicator belongs where the eye starts, not below the navigation
            it is telling you to use; and the foot is the account area, where
            a connection warning reads as an account warning. */}
        <div className="rail-top">
          <Link href={homeHref} className="brand">
            <BrandMark />
            <span className="brand-text">
              <span className="brand-name">Handbook</span>
              <span className="brand-tag">Work answers, grounded</span>
            </span>
          </Link>
          {me && showMainNav && <NotificationBell />}
        </div>

        {showMainNav && (
          <nav className="rail-nav" aria-label="Primary">
            <p className="rail-label" id="nav-explore">
              Explore
            </p>
            {(setupDone || me?.role === "member") && (
              <>
                {/* Ask STARTS a chat. With the rail's "+ New" gone it is the
                    one control for that, and Recent Chats below is how you
                    get back to an existing one.

                    The onClick is required, not belt-and-braces: navigating
                    from `/chat?c=abc` to `/chat` changes the query but does
                    not remount the page, and the param effect only fires when
                    a `c` is PRESENT — so without this the address bar would
                    lose the id while the old transcript stayed on screen. */}
                <Link
                  href="/chat"
                  className="rail-link"
                  data-active={pathname.startsWith("/chat") ? "true" : "false"}
                  aria-current={pathname.startsWith("/chat") ? "page" : undefined}
                  onClick={() => {
                    if (pathname.startsWith("/chat")) {
                      window.dispatchEvent(new CustomEvent("new-chat"));
                    }
                  }}
                >
                  <span className="rail-ico">
                    <IconAsk />
                  </span>
                  <span className="rail-link-copy">
                    <span className="rail-link-title">Ask</span>
                    <span className="rail-link-hint">Company-wide</span>
                  </span>
                </Link>
              </>
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

            {/* Below the destinations, not inside them. Sitting between "Ask"
                and "Spaces" it split one list of PLACES TO GO in half with a
                list of things already done, which is most of why the rail read
                as cluttered — three kinds of thing in one undifferentiated
                stack. */}
            {(setupDone || me?.role === "member") && <RailChats />}

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
                {/* Always shown to admins: this is where they turn Act on. */}
                <Link
                  href="/admin/actions"
                  className="rail-link"
                  data-active={pathname.startsWith("/admin/actions") ? "true" : "false"}
                  aria-current={pathname.startsWith("/admin/actions") ? "page" : undefined}
                >
                  <span className="rail-ico">
                    <IconAct />
                  </span>
                  <span className="rail-link-copy">
                    <span className="rail-link-title">Actions</span>
                    <span className="rail-link-hint">What Handbook may do</span>
                  </span>
                </Link>
                {me?.selfhosted && (
                <Link
                  href="/admin/updates"
                  className="rail-link"
                  data-active={pathname.startsWith("/admin/updates") ? "true" : "false"}
                  aria-current={pathname.startsWith("/admin/updates") ? "page" : undefined}
                >
                  <span className="rail-ico">
                    <IconUpdates />
                  </span>
                  <span className="rail-link-copy">
                    <span className="rail-link-title">Updates</span>
                    <span className="rail-link-hint">Handbook versions</span>
                  </span>
                </Link>
                )}
              </>
            )}
          </nav>
        )}

        {me && (
          <div className="rail-foot">
            <div className="rail-user" aria-label="Signed-in account">
              <div className="rail-user-profile">
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
                </div>
              </div>
              <div className="rail-user-actions">
                <Link href="/account" className="rail-user-action" title="Account">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
                    <circle cx="12" cy="8" r="3.5" stroke="currentColor" strokeWidth="1.7" />
                    <path d="M5 20c.7-4 3.3-6 7-6s6.3 2 7 6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
                  </svg>
                  <span>Account</span>
                </Link>
                <button type="button" className="rail-user-action rail-user-signout" title="Sign out" onClick={() => setConfirmOut(true)}>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
                    <path d="M10 5H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h4M14 8l4 4-4 4M9 12h9" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  <span>Sign out</span>
                </button>
              </div>
            </div>
          </div>
        )}
      </aside>

      <div className="app-main">
        {variant === "onboarding" && me && (
          // Onboarding has no sidebar, so this bar carries the brand and Sign out.
          <header className="onboarding-top">
            <span className="onboarding-brand">
              <BrandMark />
              <span>Handbook</span>
            </span>
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
            <button type="button" className="button button-secondary button-sm" onClick={() => setConfirmOut(true)}>
              Sign out
            </button>
          </header>
        )}
        <div
          id="main-content"
          className={variant === "admin" ? "admin-body" : "app-body"}
          tabIndex={-1}
        >
          {children}
        </div>
      </div>
      <NewVersionPill admin={me?.role === "admin" && Boolean(me?.selfhosted)} />
      {me && me.name === null && savedName === null && (
        <NamePrompt
          guess={guessedName ?? ""}
          onSaved={(name) => {
            setSavedName(name);
            clearMeCache();
          }}
        />
      )}
      {confirmOut && <SignOutDialog onCancel={() => setConfirmOut(false)} onConfirm={handleSignOut} />}
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

/** "Sign out of Handbook?": a modal (native <dialog>: focus stays inside, Esc cancels). */
function SignOutDialog({ onCancel, onConfirm }: { onCancel: () => void; onConfirm: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      className="signout-dialog"
      aria-labelledby="signout-title"
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
      onClick={(e) => e.target === ref.current && onCancel()}
    >
      <span className="signout-dialog-icon" aria-hidden>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
          <path d="M14 4h3.5A2.5 2.5 0 0 1 20 6.5v11a2.5 2.5 0 0 1-2.5 2.5H14M10 8l-4 4 4 4M6 12h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <h2 id="signout-title">Sign out of Handbook?</h2>
      <p>You will need to sign in again to ask questions or see your spaces.</p>
      <div className="signout-dialog-actions">
        <button type="button" className="button button-secondary" onClick={onCancel} autoFocus>
          Cancel
        </button>
        <button
          type="button"
          className="button signout-dialog-go"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            onConfirm();
          }}
        >
          {busy ? "Signing out…" : "Sign out"}
        </button>
      </div>
    </dialog>
  );
}
