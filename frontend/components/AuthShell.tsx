"use client";

import Link from "next/link";
import { AuthSceneArt } from "@/components/AuthSceneArt";
import { BrandMark } from "@/components/BrandMark";
import "@/app/site.css";

/**
 * Sign-in and sign-up, in the same frame as the home page (app/site.css):
 * the story and next steps on one side, the form card on the other.
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
    <main className="landing site">
      <header className="site-nav">
        <div className="site-wrap site-nav-inner">
          <Link href="/" className="site-brand">
            <BrandMark />
            <span>Handbook</span>
          </Link>
          <div className="site-nav-actions">
            {variant === "login" ? (
              <Link href="/signup" className="site-btn site-btn-solid">Request access</Link>
            ) : (
              <Link href="/login" className="site-btn site-btn-ghost">Sign in</Link>
            )}
          </div>
        </div>
      </header>

      <div className="site-wrap auth-stage">
        <AuthSceneArt variant={variant} />
        <div className="auth-form">
          <div className="auth-card">
            {children}
            {footer ? <div className="auth-card-foot">{footer}</div> : null}
          </div>
        </div>
      </div>
    </main>
  );
}
