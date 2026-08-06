"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { api, WorkspaceRecord } from "@/lib/api";

export default function WorkspacesPage() {
  const { me, loading } = useMe({ enforceSetupFlow: false });
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!me) return;
    api
      .listWorkspaces()
      .then(setWorkspaces)
      .finally(() => setLoadingList(false));
  }, [me]);

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
          description="Private rooms for project notes — separate from company policies."
          scene="spaces"
          meta={
            <>
              <span className="studio-chip">{workspaces.length} space{workspaces.length === 1 ? "" : "s"}</span>
              <span className="studio-chip studio-chip-ok">Org-isolated</span>
            </>
          }
        />

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
                      <h3>{w.name}</h3>
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
