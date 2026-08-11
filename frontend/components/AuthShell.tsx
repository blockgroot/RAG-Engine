"use client";

import Link from "next/link";
import { AuthSceneArt } from "@/components/AuthSceneArt";
import { BrandMark } from "@/components/BrandMark";

/**
 * Split auth layout: illustrated brand story + form card.
 * Accessible, high-contrast, motion respects prefers-reduced-motion via CSS.
 */
export function AuthShell({
  children,
  footer,
  variant = "login",
}: {
  children: React.ReactNode;
  footer?: React.ReactNode;
  variant?: "login" | "signup";
}) {
  return (
    <main className={`auth-center auth-split auth-split--${variant}`}>
      <div className="landing-atmosphere" aria-hidden>
        <span className="landing-orb landing-orb-a" />
        <span className="landing-orb landing-orb-b" />
        <span className="landing-orb landing-orb-c" />
        <span className="landing-grid" />
        <span className="landing-sheen" />
      </div>

      <header className="landing-topnav">
        <Link href="/" className="landing-brand">
          <BrandMark />
          <span>Handbook</span>
        </Link>
        <nav className="landing-topnav-actions" aria-label="Account">
          {variant === "login" ? (
            <Link href="/signup" className="button landing-cta-secondary">
              Request access
            </Link>
          ) : (
            <Link href="/login" className="landing-nav-link">
              Sign in
            </Link>
          )}
        </nav>
      </header>

      <div className="auth-split-stage">
        <AuthSceneArt variant={variant} />
        <div className="auth-split-form">
          <div className="auth-center-card">{children}</div>
          {footer ? <div className="auth-center-footer">{footer}</div> : null}
        </div>
      </div>
    </main>
  );
}
