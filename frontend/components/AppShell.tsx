"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Me } from "@/lib/api";
import { canAccessAdminPortal, isSetupComplete } from "@/lib/routing";

type Variant = "app" | "onboarding" | "admin";

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
  const setupDone = me ? isSetupComplete(me) : false;
  const showAdmin = me ? canAccessAdminPortal(me) : false;
  const homeHref = setupDone || me?.role === "member" ? "/chat" : "/onboarding";
  const showMainNav = Boolean(me && variant !== "onboarding");

  return (
    <div className={`app-shell ${showMainNav ? "app-shell-nav" : "app-shell-simple"}`}>
      <aside className="app-rail" aria-label="Workspace">
        <Link href={homeHref} className="brand">
          <span className="brand-mark" aria-hidden />
          <span className="brand-text">
            <span className="brand-name">Sourcebase</span>
            <span className="brand-tag">Grounded answers from your sources</span>
          </span>
        </Link>

        {showMainNav && (
          <nav className="rail-nav" aria-label="Primary">
            <p className="rail-label">Explore</p>
            {(setupDone || me?.role === "member") && (
              <Link
                href="/chat"
                className="rail-link"
                data-active={pathname.startsWith("/chat") ? "true" : "false"}
              >
                <span className="rail-ico" aria-hidden>?</span>
                <span className="rail-link-title">Ask</span>
              </Link>
            )}
            <Link
              href="/workspaces"
              className="rail-link"
              data-active={pathname.startsWith("/workspaces") ? "true" : "false"}
            >
              <span className="rail-ico" aria-hidden>◇</span>
              <span className="rail-link-title">Spaces</span>
            </Link>

            {showAdmin && (
              <>
                <p className="rail-label">Company</p>
                <Link
                  href="/admin/connections"
                  className="rail-link"
                  data-active={pathname.startsWith("/admin/connections") ? "true" : "false"}
                >
                  <span className="rail-ico" aria-hidden>◎</span>
                  <span className="rail-link-title">Policies</span>
                </Link>
                <Link
                  href="/admin/members"
                  className="rail-link"
                  data-active={pathname.startsWith("/admin/members") ? "true" : "false"}
                >
                  <span className="rail-ico" aria-hidden>✶</span>
                  <span className="rail-link-title">People</span>
                </Link>
              </>
            )}
          </nav>
        )}

        {me && (
          <div className="rail-foot">
            {me.org_name && <span className="org-chip">{me.org_name}</span>}
            <span className={`role-chip role-${me.role}`}>
              {me.role === "admin" ? "Admin" : "Member"}
            </span>
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
        <div className={variant === "admin" ? "admin-body" : "app-body"}>{children}</div>
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
      <span className="onboarding-step-n">{done ? "✓" : n}</span>
      <span>{label}</span>
    </div>
  );
}
