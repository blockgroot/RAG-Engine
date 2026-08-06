"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
          <span>Folio</span>
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

      <section className="landing-hero" aria-labelledby="landing-title">
        <p className="landing-eyebrow">Work AI that works</p>
        <h1 id="landing-title" className="landing-title">
          Folio
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
          Notion · Google Drive · GitHub
        </p>
      </section>

      <section className="landing-strip" aria-label="How Folio helps">
        <div className="landing-strip-item">
          <strong>Ask once</strong>
          <span>Policies and repositories in one place</span>
        </div>
        <div className="landing-strip-item">
          <strong>Stay grounded</strong>
          <span>Every answer shows where it came from</span>
        </div>
        <div className="landing-strip-item">
          <strong>Stay private</strong>
          <span>Tenant isolation on every request</span>
        </div>
      </section>
    </main>
  );
}
