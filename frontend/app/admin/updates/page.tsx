"use client";

/**
 * Admin-only: which Handbook version this copy runs, and what is newer.
 *
 * Cloud is updated by us, so its admins only read what changed. A self-hosted
 * copy offers the update: the button asks the updater container to back up,
 * switch versions and restart, which takes this API down for a minute. The
 * progress poll therefore treats a failed request as "restarting", not as an
 * error, and reloads the page once the updater reports the new version is up.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { PageHeader } from "@/components/PageHeader";
import { useMe } from "@/lib/useMe";
import { ApiError, api, type Release, type UpdateInfo, type UpdateProgress } from "@/lib/api";

const POLL_MS = 3000;

const STEP_LABEL: Record<string, string> = {
  backup: "Backing up the database",
  pull: "Downloading the new version",
  restart: "Restarting Handbook",
  health: "Waiting for Handbook to start",
  rollback: "Something went wrong; going back to the previous version",
};

function ReleaseNotes({ release }: { release: Release }) {
  return (
    <div className="stack" style={{ gap: "0.35rem" }}>
      <p>
        <strong>Handbook {release.version}</strong>
        {release.date && <span className="muted"> · {release.date}</span>}
        {release.security && <span className="studio-chip"> Security fix</span>}
        {release.manual_steps && <span className="studio-chip"> Needs steps on your server</span>}
      </p>
      {release.notes.length > 0 && (
        <ul>
          {release.notes.map((note, i) => (
            <li key={i}>{note}</li>
          ))}
        </ul>
      )}
      {release.notes_url && (
        <a href={release.notes_url} target="_blank" rel="noreferrer">
          Full release notes
        </a>
      )}
    </div>
  );
}

export default function AdminUpdatesPage() {
  const { me, loading } = useMe({ requireAdmin: true });
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [progress, setProgress] = useState<UpdateProgress | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    try {
      const next = await api.getUpdateProgress();
      setRestarting(false);
      setProgress(next);
      if (next.state === "done") {
        window.setTimeout(() => window.location.reload(), 2000);
        return;
      }
      if (next.state !== "running") return;
    } catch {
      // The API itself is being restarted by the update.
      setRestarting(true);
    }
    timer.current = setTimeout(poll, POLL_MS);
  }, []);

  useEffect(() => {
    if (!me) return;
    api
      .getUpdates()
      .then((next) => {
        setInfo(next);
        // An update started earlier (or in another tab) is still shown.
        if (next.deployment === "selfhosted") {
          api
            .getUpdateProgress()
            .then((p) => {
              if (p.state === "running") void poll();
              else if (p.state === "failed" || p.state === "rolled_back") setProgress(p);
            })
            .catch(() => undefined);
        }
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not load this page."));
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [me, poll]);

  if (loading || !me) {
    return (
      <main className="page">
        <p className="muted">Loading&hellip;</p>
      </main>
    );
  }

  async function update(version: string) {
    if (
      !window.confirm(
        `Update Handbook to ${version}? It takes a few minutes, and Handbook is unavailable while it restarts. The database is backed up first.`,
      )
    ) {
      return;
    }
    setError(null);
    try {
      setProgress(await api.startUpdate(version));
      void poll();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the update.");
    }
  }

  const selfhosted = info?.deployment === "selfhosted";
  const latest = info?.newer[0] ?? null;
  const running = progress?.state === "running";

  return (
    <AppShell me={me} variant="admin">
      <main className="page-wide studio-page stack">
        <PageHeader
          eyebrow="Company"
          title="Updates"
          description={
            selfhosted
              ? "New versions of Handbook, and updating this server to them."
              : "Handbook Cloud is kept up to date by the Handbook team. Here is what changed."
          }
          meta={
            info?.current ? (
              <span className={`studio-chip${latest ? "" : " studio-chip-ok"}`}>
                {latest ? `Update available: ${latest.version}` : `Up to date: ${info.current}`}
              </span>
            ) : null
          }
        />

        {error && (
          <div className="banner banner-warn" role="alert">
            {error}
          </div>
        )}

        {(running || restarting) && (
          <div className="banner banner-ok" role="status">
            Updating to {progress?.version}:{" "}
            {restarting ? "Restarting Handbook" : STEP_LABEL[progress?.step ?? ""] ?? "Working"}
            &hellip; Keep this page open.
          </div>
        )}
        {progress?.state === "done" && (
          <div className="banner banner-ok" role="status">
            Handbook is now on {progress.version}. Reloading&hellip;
          </div>
        )}
        {(progress?.state === "failed" || progress?.state === "rolled_back") && (
          <div className="banner banner-warn" role="alert">
            The update to {progress.version} did not finish
            {progress.state === "rolled_back" ? ", so Handbook went back to the previous version" : ""}.{" "}
            {progress.error} Nothing was lost: a backup was taken before it started.
          </div>
        )}

        {info && (
          <section className="studio-panel stack">
            <div className="studio-section-head">
              <h2>{info.current ? `You are on Handbook ${info.current}` : "Version not known"}</h2>
            </div>
            {!info.current && (
              <p className="muted">
                This copy does not report its version, so new versions cannot be checked.
              </p>
            )}
            {info.current && !latest && <p className="muted">This is the latest version.</p>}
          </section>
        )}

        {info && latest && selfhosted && (
          <section className="studio-panel stack">
            <div className="studio-section-head">
              <h2>Handbook {latest.version} is available</h2>
            </div>
            {info.newer.map((release) => (
              <ReleaseNotes key={release.version} release={release} />
            ))}
            {info.can_update ? (
              <div>
                <button
                  type="button"
                  className="button"
                  disabled={running || restarting}
                  onClick={() => void update(latest.version)}
                >
                  Update to {latest.version}
                </button>
              </div>
            ) : (
              <div className="stack" style={{ gap: "0.35rem" }}>
                {info.blocked_reason && <p>{info.blocked_reason}</p>}
                <p className="muted">On the server, in the Handbook folder:</p>
                <pre>
                  <code>./upgrade.sh {latest.version}</code>
                </pre>
              </div>
            )}
          </section>
        )}

        {info?.whats_new && (
          <section className="studio-panel stack">
            <div className="studio-section-head">
              <h2>What&rsquo;s new</h2>
            </div>
            <ReleaseNotes release={info.whats_new} />
          </section>
        )}
      </main>
    </AppShell>
  );
}
