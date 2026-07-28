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

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header-inner">
          <Link href={setupDone || me?.role === "member" ? "/chat" : "/onboarding"} className="brand">
            <span className="brand-mark" aria-hidden />
            Policy Portal
          </Link>

          {me && variant !== "onboarding" && (
            <nav className="app-nav" aria-label="Primary">
              {(setupDone || me.role === "member") && (
                <Link href="/chat" data-active={pathname.startsWith("/chat")}>
                  Ask
                </Link>
              )}
              {showAdmin && (
                <>
                  <Link
                    href="/admin/connections"
                    data-active={pathname.startsWith("/admin/connections")}
                  >
                    Sources
                  </Link>
                  <Link href="/admin/members" data-active={pathname.startsWith("/admin/members")}>
                    Team
                  </Link>
                </>
              )}
            </nav>
          )}

          {me && (
            <div className="app-header-meta">
              {me.org_name && <span className="org-chip">{me.org_name}</span>}
              <span className={`role-chip role-${me.role}`}>
                {me.role === "admin" ? "Admin" : "Member"}
              </span>
            </div>
          )}
        </div>

        {variant === "onboarding" && me && (
          <div className="onboarding-progress" aria-label="Setup progress">
            <Step n={1} label="Connect" done={me.has_connection} active={!me.has_connection} />
            <Step
              n={2}
              label="Sync policies"
              done={me.has_documents}
              active={me.has_connection && !me.has_documents}
            />
            <Step
              n={3}
              label="Invite team"
              done={false}
              active={me.has_connection && me.has_documents}
            />
          </div>
        )}
      </header>

      <div className={variant === "admin" ? "admin-body" : "app-body"}>{children}</div>
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
