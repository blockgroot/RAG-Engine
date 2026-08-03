"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
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
      setError(err instanceof Error ? err.message : "Could not create the workspace.");
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
        <div>
          <p className="eyebrow">Workspace within a workspace</p>
          <h1>My Workspaces</h1>
          <p className="muted">
            Create a personal space for a specific topic — like meeting notes — invite a
            few colleagues, and connect a Notion page or Drive folder just for them.
            Questions asked in a workspace are answered only from that workspace&rsquo;s own
            content, never the rest of your organization&rsquo;s policies.
          </p>
        </div>

        <form onSubmit={handleCreate} className="card stack" style={{ maxWidth: 480 }}>
          <div className="field">
            <label htmlFor="workspace-name">New workspace name</label>
            <input
              id="workspace-name"
              className="input"
              type="text"
              placeholder="e.g. Q3 Planning Meeting Notes"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={creating}
            />
          </div>
          {error && <div className="banner banner-warn">{error}</div>}
          <button className="button" type="submit" disabled={creating || !name.trim()}>
            {creating ? "Creating…" : "Create workspace"}
          </button>
        </form>

        <div className="stack">
          {loadingList ? (
            <p className="muted">Loading your workspaces…</p>
          ) : workspaces.length === 0 ? (
            <p className="muted">You&rsquo;re not in any workspaces yet — create one above.</p>
          ) : (
            workspaces.map((w) => (
              <Link key={w.id} href={`/workspaces/${w.id}`} className="card" style={{ display: "block" }}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: "1rem",
                  }}
                >
                  <h3 style={{ fontSize: "1.05rem" }}>{w.name}</h3>
                  <span className="badge">{w.role === "owner" ? "Owner" : "Member"}</span>
                </div>
              </Link>
            ))
          )}
        </div>
      </main>
    </AppShell>
  );
}
