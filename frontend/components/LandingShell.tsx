import type { ReactNode } from "react";
import Link from "next/link";
import { BrandMark } from "@/components/BrandMark";
import "@/app/site.css";

/**
 * Public chrome for the marketing page. White, hairline rules, square
 * buttons: the product figures carry the colour, the frame stays quiet.
 * Styles live in app/site.css under `.site`, apart from the app's globals.
 */
export function LandingShell({ children }: { children: ReactNode }) {
  return (
    <main className="landing site">
      <header className="site-nav">
        <div className="site-wrap site-nav-inner">
          <Link href="/" className="site-brand">
            <BrandMark />
            <span>Handbook</span>
          </Link>
          <nav className="site-nav-links" aria-label="Page">
            <Link href="/#how" className="site-nav-link">How it works</Link>
            <Link href="/#modes" className="site-nav-link">Modes</Link>
            <Link href="/#security" className="site-nav-link">Security</Link>
            <Link href="/#results" className="site-nav-link">Results</Link>
          </nav>
          <div className="site-nav-actions">
            <Link href="/login" className="site-btn site-btn-ghost site-hide-sm">
              Sign in
            </Link>
            <Link href="/signup" className="site-btn site-btn-solid">
              Request access
            </Link>
          </div>
        </div>
      </header>

      {children}

      <footer className="site-foot">
        <div className="site-wrap">
          <div className="site-foot-top">
            <div>
              <Link href="/" className="site-brand">
                <BrandMark />
                <span>Handbook</span>
              </Link>
              <p className="site-foot-tag">Answers from your company&rsquo;s tools, with the source.</p>
            </div>
            <nav className="site-foot-links" aria-label="Footer">
              <Link href="/#how">How it works</Link>
              <Link href="/#modes">Modes</Link>
              <Link href="/#security">Security</Link>
              <Link href="/#results">Results</Link>
              <Link href="/login">Sign in</Link>
              <Link href="/signup" className="site-foot-cta">Request access</Link>
            </nav>
          </div>
          <p className="site-foot-base">&copy; 2026 Handbook</p>
        </div>
      </footer>
    </main>
  );
}
