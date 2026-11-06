"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { useMe } from "@/lib/useMe";
import { api, WorkspaceDetail, WorkspaceRecord } from "@/lib/api";
import { SourceLogos, SpaceMark, spaceSources, spaceStyle } from "@/components/SpaceMark";

const STEP_SVG = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

const STEPS = [
  {
    title: "Create a room",
    copy: "Name it after a team or project.",
    icon: (
      <svg {...STEP_SVG}>
        <rect x="3" y="3" width="7" height="7" rx="2" />
        <rect x="14" y="3" width="7" height="7" rx="2" />
        <rect x="3" y="14" width="7" height="7" rx="2" />
        <path d="M17.5 14v7M14 17.5h7" />
      </svg>
    ),
  },
  {
    title: "Connect its sources",
    copy: "Notion, Drive, Slack, Linear or GitHub, for this room only.",
    icon: (
      <svg {...STEP_SVG}>
        <path d="M9 15l6-6" />
        <path d="M11 6l1-1a4.2 4.2 0 0 1 6 6l-1 1" />
        <path d="M13 18l-1 1a4.2 4.2 0 0 1-6-6l1-1" />
      </svg>
    ),
  },
  {
    title: "Invite and ask",
    copy: "Members get answers from this room’s documents.",
    icon: (
      <svg {...STEP_SVG}>
        <circle cx="9" cy="8" r="3.5" />
        <path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" />
        <path d="M17 8v6M14 11h6" />
      </svg>
    ),
  },
];

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
  // Each room's sources and readiness, for its card. One request per space;
  // a card whose detail fails simply shows no logos.
  // ponytail: N requests, fold these fields into GET /workspaces if lists grow long.
  const [details, setDetails] = useState<Record<string, WorkspaceDetail>>({});
  const [name, setName] = useState("");
  const nameInput = useRef<HTMLInputElement>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listWorkspaces()
      .then((rows) => {
        if (cancelled) return;
        setWorkspaces(rows);
        rows.forEach((w) =>
          api
            .getWorkspace(w.id)
            .then((d) => !cancelled && setDetails((prev) => ({ ...prev, [w.id]: d })))
            .catch(() => undefined),
        );
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
      <main className="spaces-page">
        <header className="spaces-hero">
          <span className="ask-scope">
            <span className="ask-scope-dot" aria-hidden />
            Spaces
          </span>
          <h1>
            Team <span>spaces</span>
          </h1>
          <p className="ask-hello-copy">
            Private rooms with their own sources and people. Answers stay inside the room, apart from
            company-wide documents.
          </p>
          <form onSubmit={handleCreate} className="spaces-new" aria-label="New space">
            <label className="sr-only" htmlFor="workspace-name">
              Space name
            </label>
            <input
              ref={nameInput}
              id="workspace-name"
              type="text"
              placeholder="Name a new space, e.g. Q3 planning notes"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={creating}
            />
            <button className="button" type="submit" disabled={creating || !name.trim()}>
              {creating ? "Creating…" : "Create space"}
            </button>
          </form>
          {error && (
            <div className="banner banner-warn" role="alert">
              {error}
            </div>
          )}
        </header>

        {notice && <div className="banner banner-warn">{notice}</div>}

        <ol className="spaces-flow" aria-label="How a space works">
          {STEPS.map((s, i) => (
            <li key={s.title} style={{ ["--i" as string]: i }}>
              <span className="spaces-flow-icon" aria-hidden>
                {s.icon}
              </span>
              <strong>{s.title}</strong>
              <span className="muted">{s.copy}</span>
            </li>
          ))}
        </ol>

        <section className="spaces-section" aria-labelledby="your-spaces-title">
          <div className="spaces-section-head">
            <h2 id="your-spaces-title">Your spaces</h2>
            {!loadingList && <span className="spaces-count">{workspaces.length}</span>}
          </div>
          {loadingList ? (
            <div className="spaces-grid" aria-busy="true">
              <div className="studio-skeleton" />
              <div className="studio-skeleton" />
              <div className="studio-skeleton" />
            </div>
          ) : (
            <ul className="spaces-grid">
              {workspaces.map((w, i) => {
                const d = details[w.id];
                const sources = spaceSources(d);
                const status = !d
                  ? null
                  : d.sync_in_progress
                    ? ["is-sync", "Importing documents"]
                    : d.ready_to_ask || d.github_connected
                      ? ["is-ready", "Ready to ask"]
                      : ["is-idle", "No sources yet"];
                return (
                  <li key={w.id} className="room-card" style={{ ...spaceStyle(w.name), ["--i" as string]: i }}>
                    <div className="room-card-band" aria-hidden>
                      <span className={`spaces-role${w.role === "owner" ? " is-owner" : ""}`}>
                        {w.role === "owner" ? "Owner" : "Member"}
                      </span>
                    </div>
                    <SpaceMark name={w.name} className="room-card-mark" />
                    <strong className="room-card-name" title={w.name}>
                      {w.name}
                    </strong>
                    <span className="room-card-meta">
                      {status ? (
                        <span className={`room-status ${status[0]}`}>{status[1]}</span>
                      ) : (
                        <span className="room-status">&nbsp;</span>
                      )}
                      <SourceLogos sources={sources} />
                    </span>
                    <div className="room-card-actions">
                      {/* EVERYONE lands on Ask, owners included: asking is what
                          a space is for; settings are the owner's second link. */}
                      <Link href={`/workspaces/${w.id}/ask`} className="button button-sm">
                        Ask →
                      </Link>
                      {w.role === "owner" && (
                        <Link href={`/workspaces/${w.id}`} className="button button-secondary button-sm">
                          Settings
                        </Link>
                      )}
                    </div>
                  </li>
                );
              })}
              <li className="room-card spaces-card-new" style={{ ["--i" as string]: workspaces.length }}>
                <button type="button" onClick={() => nameInput.current?.focus()}>
                  <span className="spaces-plus" aria-hidden>
                    +
                  </span>
                  <strong>New space</strong>
                  <span className="muted">A room for one team or project</span>
                </button>
              </li>
            </ul>
          )}
        </section>
      </main>
    </AppShell>
  );
}
