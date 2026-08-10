"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LandingProductArt } from "@/components/LandingProductArt";
import { api } from "@/lib/api";
import { homePathFor } from "@/lib/routing";

/**
 * Public product landing (Glean-style single composition).
 * Signed-in users are forwarded into the app; everyone else sees the hero.
 */
export default function RootPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (!cancelled) router.replace(homePathFor(me));
      })
      .catch(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (!ready) {
    return (
      <main className="landing">
        <p className="landing-loading muted">Loading…</p>
      </main>
    );
  }

  return (
    <main className="landing">
      <div className="landing-atmosphere" aria-hidden>
        <span className="landing-orb landing-orb-a" />
        <span className="landing-orb landing-orb-b" />
        <span className="landing-orb landing-orb-c" />
        <span className="landing-grid" />
        <span className="landing-sheen" />
      </div>

      <header className="landing-topnav">
        <Link href="/" className="landing-brand">
          <span className="brand-mark" aria-hidden />
          <span>Handbook</span>
        </Link>
        <nav className="landing-topnav-actions" aria-label="Account">
          <Link href="/login" className="landing-nav-link">
            Sign in
          </Link>
          <Link href="/signup" className="button landing-cta-secondary">
            Request access
          </Link>
        </nav>
      </header>

      <section className="landing-hero landing-hero-split" aria-labelledby="landing-title">
        <div className="landing-hero-copy">
          <p className="landing-eyebrow">Work AI that works</p>
          <h1 id="landing-title" className="landing-title">
            Handbook
          </h1>
          <p className="landing-subtitle">
            Ask your company&rsquo;s policies and code — answers stay grounded in
            what you connected.
          </p>
          <div className="landing-cta-row">
            <Link href="/login" className="button landing-cta-primary">
              Sign in
            </Link>
            <Link href="/signup" className="button button-secondary landing-cta-ghost">
              Set up your company
            </Link>
          </div>
          <p className="landing-connectors" aria-label="Supported sources">
            <span className="landing-connector-pill">Notion</span>
            <span className="landing-connector-pill">Google Drive</span>
            <span className="landing-connector-pill">GitHub</span>
          </p>
        </div>
        <LandingProductArt />
      </section>

      <section className="landing-strip" aria-label="How Handbook helps">
        <div className="landing-strip-item">
          <span className="landing-strip-icon" aria-hidden>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="1.75" />
              <path d="M16.5 16.5 21 21" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
            </svg>
          </span>
          <strong>Ask once</strong>
          <span>Policies and repositories in one place</span>
        </div>
        <div className="landing-strip-item">
          <span className="landing-strip-icon" aria-hidden>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path d="M12 3v18M5 8l7-5 7 5M5 16l7 5 7-5" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <strong>Stay grounded</strong>
          <span>Every answer shows where it came from</span>
        </div>
        <div className="landing-strip-item">
          <span className="landing-strip-icon" aria-hidden>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <rect x="4" y="10" width="16" height="10" rx="2" stroke="currentColor" strokeWidth="1.75" />
              <path d="M8 10V7a4 4 0 0 1 8 0v3" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
            </svg>
          </span>
          <strong>Stay private</strong>
          <span>Tenant isolation on every request</span>
        </div>
      </section>
    </main>
  );
}
