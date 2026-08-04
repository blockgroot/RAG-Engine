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
      <main className="page-wide stack">
        <PageHeader
          eyebrow="Spaces"
          title="Your team spaces"
          description="Private rooms for project notes — separate from company policies."
        />

        <section className="panel">
          <div className="panel-head">
            <h2>New space</h2>
          </div>
          <form onSubmit={handleCreate} className="stack" style={{ maxWidth: 480 }}>
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
            <button className="button" type="submit" disabled={creating || !name.trim()} style={{ width: "fit-content" }}>
              {creating ? "Creating…" : "Create space"}
            </button>
          </form>
        </section>

        <section className="stack">
          <div className="panel-head" style={{ marginBottom: 0 }}>
            <h2>Your spaces</h2>
          </div>
          {loadingList ? (
            <p className="muted">Loading your spaces…</p>
          ) : workspaces.length === 0 ? (
            <p className="muted">No spaces yet — create one above to get started.</p>
          ) : (
            <div className="tile-grid">
              {workspaces.map((w) => (
                <Link key={w.id} href={`/workspaces/${w.id}`} className="workspace-tile">
                  <div className="workspace-tile-top">
                    <h3>{w.name}</h3>
                    <span className="badge">{w.role === "owner" ? "Owner" : "Member"}</span>
                  </div>
                  <span className="workspace-tile-cta">Open →</span>
                </Link>
              ))}
            </div>
          )}
        </section>
      </main>
    </AppShell>
  );
}
