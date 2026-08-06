"use client";

import Link from "next/link";

/** Centered auth form over a full-bleed atmosphere — no split columns. */
export function AuthShell({
  children,
  footer,
}: {
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <main className="auth-center">
      <div className="landing-atmosphere" aria-hidden>
        <span className="landing-orb landing-orb-a" />
        <span className="landing-orb landing-orb-b" />
        <span className="landing-orb landing-orb-c" />
        <span className="landing-grid" />
      </div>

      <header className="landing-topnav">
        <Link href="/" className="landing-brand">
          <span className="brand-mark" aria-hidden />
          <span>Folio</span>
        </Link>
        <Link href="/" className="landing-nav-link">
          Home
        </Link>
      </header>

      <div className="auth-center-stage">
        <div className="auth-center-card">{children}</div>
        {footer ? <div className="auth-center-footer">{footer}</div> : null}
      </div>
    </main>
  );
}
