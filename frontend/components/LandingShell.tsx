import type { ReactNode } from "react";
import Link from "next/link";
import { BrandMark } from "@/components/BrandMark";

/** Shared public chrome so home and How it works stay on the same grid. */
export function LandingShell({
  children,
  active = "home",
}: {
  children: ReactNode;
  active?: "home" | "how";
}) {
  return (
    <main className="landing landing-story">
      <div className="landing-atmosphere" aria-hidden>
        <span className="landing-orb landing-orb-a" />
        <span className="landing-orb landing-orb-b" />
        <span className="landing-orb landing-orb-c" />
        <span className="landing-grid" />
        <span className="landing-sheen" />
      </div>

      <header className="landing-topnav">
        <div className="landing-wrap landing-topnav-inner">
          <Link href="/" className="landing-brand">
            <BrandMark />
            <span>Handbook</span>
          </Link>
          <nav className="landing-topnav-actions" aria-label="Page">
            <Link
              href="/#what"
              className={`landing-nav-link${active === "home" ? " is-active" : ""}`}
            >
              Product
            </Link>
            <Link
              href="/how-it-works"
              className={`landing-nav-link${active === "how" ? " is-active" : ""}`}
            >
              How it works
            </Link>
            <Link href="/login" className="landing-nav-link">
              Sign in
            </Link>
            <Link href="/signup" className="button landing-cta-secondary">
              Request access
            </Link>
          </nav>
        </div>
      </header>

      {children}
    </main>
  );
}
