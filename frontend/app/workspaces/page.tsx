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

        <div className="spaces-layout">
          <section className="studio-panel create-space-panel" aria-labelledby="new-space-title">
            <div className="studio-panel-glow" aria-hidden />
            <div className="studio-section-head">
              <h2 id="new-space-title">Create a space</h2>
              <p className="muted">
                Invite teammates later — answers stay inside this room only.
              </p>
            </div>
            <form onSubmit={handleCreate} className="create-space-form">
              <div className="field">
                <label htmlFor="workspace-name">Name</label>
                <input
                  id="workspace-name"
                  className="input"
                  type="text"
                  placeholder="e.g. Q3 planning notes"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  disabled={creating}
                />
              </div>
              {error && <div className="banner banner-warn">{error}</div>}
              <button
                className="button"
                type="submit"
                disabled={creating || !name.trim()}
              >
                {creating ? "Creating…" : "Create space"}
              </button>
            </form>
          </section>

          <section className="studio-section spaces-list-section" aria-labelledby="your-spaces-title">
            <div className="studio-section-head">
              <h2 id="your-spaces-title">Your spaces</h2>
              <p className="muted">Open a room to connect sources, invite people, and ask.</p>
            </div>
            {loadingList ? (
              <div className="studio-skeleton-grid" aria-busy="true">
                <div className="studio-skeleton" />
                <div className="studio-skeleton" />
              </div>
            ) : workspaces.length === 0 ? (
              <div className="studio-empty">
                <div className="studio-empty-mark" aria-hidden />
                <h3>No spaces yet</h3>
                <p className="muted">Create one on the left to start a private notes room.</p>
              </div>
            ) : (
              <div className="tile-grid spaces-bento">
                {workspaces.map((w, i) => (
                  <Link
                    key={w.id}
                    href={`/workspaces/${w.id}`}
                    className="workspace-tile"
                    style={{ animationDelay: `${0.08 + i * 0.06}s` }}
                  >
                    <span className="workspace-tile-mark" aria-hidden>
                      {(w.name || "S").trim().charAt(0).toUpperCase()}
                    </span>
                    <div className="workspace-tile-top">
                      <h3 title={w.name}>{w.name}</h3>
                      <span className="badge">{w.role === "owner" ? "Owner" : "Member"}</span>
                    </div>
                    <span className="workspace-tile-cta">Open space</span>
                  </Link>
                ))}
              </div>
            )}
          </section>
        </div>
      </main>
    </AppShell>
  );
}
