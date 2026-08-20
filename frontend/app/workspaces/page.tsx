"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { api, WorkspaceRecord } from "@/lib/api";

export default function WorkspacesPage() {
  return (
    <Suspense
      fallback={
        <main className="page">
          <p className="muted">Loading…</p>
        </main>
      }
    >
      <WorkspacesPageInner />
    </Suspense>
  );
}

/**
 * Curated two-tone identities, one per space. Cycling a fixed, hand-picked
 * palette (rather than a raw HSL hash) keeps every card confidently
 * saturated while the app's teal stays the dominant brand color everywhere
 * else — cards get personality, chrome doesn't get diluted.
 */
const SPACE_PALETTE: [string, string][] = [
  ["#14b8a6", "#0f766e"], // teal (brand)
  ["#6366f1", "#4338ca"], // indigo
  ["#f97316", "#c2410c"], // amber
  ["#ec4899", "#be185d"], // pink
  ["#0ea5e9", "#0369a1"], // sky
  ["#84cc16", "#4d7c0f"], // lime
];

function paletteFor(id: string): [string, string] {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) | 0;
  return SPACE_PALETTE[Math.abs(hash) % SPACE_PALETTE.length];
}

function WorkspacesPageInner() {
  const { me, loading } = useMe({ enforceSetupFlow: false });
  const searchParams = useSearchParams();
  /*
   * A GitHub connect whose redirect could not be completed lands HERE when the
   * OAuth `state` was lost, because at that point the backend cannot tell which
   * space (or the org) started it — and /admin/connections would bounce a
   * non-admin space owner. Without this banner the user would arrive at a normal
   * Spaces list with no idea why, which is the dead end being fixed.
   */
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    if (searchParams.get("connect_error") === "github_finish_connect") {
      setNotice(
        "Almost there — GitHub sent you back without the details needed to link the account. " +
          "Open the space (or Company → Sources) and click Connect once more; the app is already " +
          "installed on your GitHub, so you should not need to install anything again."
      );
    }
  }, [searchParams]);
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fired on mount rather than gated on `me`, so the session lookup and the
  // list load run CONCURRENTLY instead of as a two-round-trip waterfall. Both
  // are authenticated by the same cookie, so there is nothing to wait for; if
  // the caller turns out to be unauthenticated this 401s harmlessly and
  // useMe's guard does the redirect.
  useEffect(() => {
    let cancelled = false;
    api
      .listWorkspaces()
      .then((rows) => {
        if (!cancelled) setWorkspaces(rows);
      })
      .catch(() => {
        /* useMe owns the auth redirect; an empty list is the right fallback */
      })
      .finally(() => {
        if (!cancelled) setLoadingList(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || creating) return;
    setCreating(true);
    setError(null);
    try {
      const created = await api.createWorkspace(trimmed);
      setWorkspaces((prev) => [
        { id: created.id, name: created.name, role: "owner", created_by: null },
        ...prev,
      ]);
      setName("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn’t create that space. Try again.");
    } finally {
      setCreating(false);
    }
  }

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  return (
    <AppShell me={me} variant="app">
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Spaces"
          title="Your team spaces"
          description="Private rooms for project notes — separate from company-wide documents."
          scene="spaces"
          meta={
            <>
              <span className="studio-chip">{workspaces.length} space{workspaces.length === 1 ? "" : "s"}</span>
              <span className="studio-chip studio-chip-ok">Org-isolated</span>
            </>
          }
        />

        {notice && <div className="banner banner-warn">{notice}</div>}

        <section className="spaces-hub" aria-labelledby="your-spaces-title">
          <div className="studio-section-head">
            <h2 id="your-spaces-title">Your spaces</h2>
            <p className="muted">Open a space to connect sources, invite people, and ask.</p>
          </div>

          {loadingList ? (
            <div className="studio-skeleton-grid" aria-busy="true">
              <div className="studio-skeleton" />
              <div className="studio-skeleton" />
              <div className="studio-skeleton" />
            </div>
          ) : (
            <div className="spaces-grid">
              <div className="space-card space-card-new">
                <span className="space-card-new-icon" aria-hidden>+</span>
                <h3>New space</h3>
                <p className="muted">Invite teammates later — answers stay inside this room only.</p>
                <form onSubmit={handleCreate}>
                  <label className="sr-only" htmlFor="workspace-name">Name</label>
                  <input
                    id="workspace-name"
                    className="input"
                    type="text"
                    placeholder="e.g. Q3 planning notes"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    disabled={creating}
                  />
                  {error && <div className="banner banner-warn">{error}</div>}
                  <button className="button" type="submit" disabled={creating || !name.trim()}>
                    {creating ? "Creating…" : "Create space"}
                  </button>
                </form>
              </div>

              {workspaces.map((w, i) => {
                const [a, b] = paletteFor(w.id);
                return (
                  <Link
                    key={w.id}
                    href={`/workspaces/${w.id}`}
                    className="space-card"
                    style={{ "--card-a": a, "--card-b": b, animationDelay: `${0.06 + i * 0.05}s` } as React.CSSProperties}
                  >
                    <div className="space-card-top">
                      <span className="space-card-mark" aria-hidden>
                        {(w.name || "S").trim().charAt(0).toUpperCase()}
                      </span>
                      <span className="badge">{w.role === "owner" ? "Owner" : "Member"}</span>
                    </div>
                    <div className="space-card-body">
                      <h3 title={w.name}>{w.name}</h3>
                    </div>
                    <div className="space-card-foot">
                      <span className="space-card-cta">Open space</span>
                    </div>
                  </Link>
                );
              })}
            </div>
          )}
        </section>
      </main>
    </AppShell>
  );
}
