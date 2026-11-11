"use client";

/**
 * Where Connect lands when the tool's keys are not set on this server.
 *
 * The connect route used to answer a JSON 400, which a browser shows as a
 * blank page. Google and Slack use the company's own app (set up in Sources);
 * Notion, Linear and GitHub use keys whoever runs Handbook sets once.
 */

import { Suspense, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";

import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { CopyField } from "@/components/OwnAppSetup";

type Guide = {
  name: string;
  glyph: BrandName;
  /** The company's own app, set up by an admin in Sources. */
  ownApp?: boolean;
  steps?: { title: string; body: ReactNode; href?: string; link?: string }[];
  envs?: string[];
};

const GUIDES: Record<string, Guide> = {
  google: { name: "Google Drive", glyph: "drive", ownApp: true },
  slack: { name: "Slack", glyph: "slack", ownApp: true },
  notion: {
    name: "Notion",
    glyph: "notion",
    steps: [
      {
        title: "Create a public integration in Notion",
        body: <>Choose <b>New integration</b>, set its type to <b>Public</b>, and name it Handbook.</>,
        href: "https://www.notion.so/profile/integrations",
        link: "Open Notion integrations",
      },
      { title: "Add the address below as its Redirect URI", body: <>Notion sends people back here after they sign in.</> },
      { title: "Copy its OAuth client ID and secret into Handbook's settings", body: <>Then restart Handbook.</> },
    ],
    envs: ["NOTION_CLIENT_ID", "NOTION_CLIENT_SECRET"],
  },
  linear: {
    name: "Linear",
    glyph: "linear",
    steps: [
      {
        title: "Create an OAuth application in Linear",
        body: <>Name it Handbook. Any workspace admin can do this.</>,
        href: "https://linear.app/settings/api/applications/new",
        link: "Open Linear",
      },
      { title: "Add the address below as its Callback URL", body: <>Linear sends people back here after they sign in.</> },
      { title: "Copy its client ID and secret into Handbook's settings", body: <>Then restart Handbook.</> },
    ],
    envs: ["LINEAR_CLIENT_ID", "LINEAR_CLIENT_SECRET"],
  },
  github: {
    name: "GitHub",
    glyph: "github",
    steps: [
      {
        title: "Create a GitHub App",
        body: (
          <>
            Repository permissions: <b>Contents</b>, <b>Metadata</b> and <b>Pull requests</b> read, and{" "}
            <b>Issues</b> read and write (for Agent mode).
          </>
        ),
        href: "https://github.com/settings/apps/new",
        link: "Open GitHub",
      },
      { title: "Add the address below as its Callback URL", body: <>Tick <b>Request user authorization during installation</b>.</> },
      { title: "Copy the app's name, client ID, secret and private key into Handbook's settings", body: <>Then restart Handbook.</> },
    ],
    envs: ["GITHUB_APP_SLUG", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GITHUB_APP_PRIVATE_KEY"],
  },
};

function SetupInner() {
  const params = useSearchParams();
  const provider = params.get("provider") ?? "";
  const space = params.get("space");
  const guide = GUIDES[provider];
  const back = space ? `/workspaces/${encodeURIComponent(space)}` : "/admin/connections";
  const callback = typeof window === "undefined" ? "" : `${window.location.origin}/api/auth/${provider}/callback`;

  if (!guide) {
    return (
      <section className="gh-choose-card">
        <h1>That tool can&apos;t be connected yet</h1>
        <a className="gh-choose-back" href={back}>← Back</a>
      </section>
    );
  }

  return (
    <section className="gh-choose-card setup-card">
      <span className="gh-choose-badge" aria-hidden>
        <BrandGlyph name={guide.glyph} size={30} />
      </span>
      <p className="gh-choose-eyebrow">Connect {guide.name}</p>
      <h1>{guide.ownApp ? `First, your company's ${provider === "google" ? "Google" : "Slack"} app` : `${guide.name} isn't set up on this server yet`}</h1>

      {guide.ownApp ? (
        <>
          <p className="gh-choose-hint">
            {guide.name} connects through an app your company owns. A company admin sets it up once in
            Sources, in about {provider === "google" ? 10 : 5} minutes, with each step shown there.
            Then come back and press Connect.
          </p>
          <div className="setup-actions">
            <a className="button" href="/admin/connections">Set it up in Sources</a>
            {space && <p className="setup-note">Not an admin? Ask one to do this step.</p>}
          </div>
        </>
      ) : (
        <>
          <p className="gh-choose-hint">
            Whoever runs Handbook does this once, in about 5 minutes. After that, anyone allowed to
            connect sources just presses Connect.
          </p>
          <ol className="setup-steps">
            {guide.steps!.map((step, i) => (
              <li key={step.title} style={{ ["--i" as string]: i }}>
                <b>{i + 1}</b>
                <div>
                  <strong>{step.title}</strong>
                  <p>{step.body}</p>
                  {step.href && (
                    <a className="button button-secondary" href={step.href} target="_blank" rel="noreferrer">
                      {step.link} ↗
                    </a>
                  )}
                  {i === 1 && callback && <CopyField label="Address" value={callback} />}
                  {i === 2 && (
                    <div className="setup-envs">
                      {guide.envs!.map((env) => (
                        <code key={env}>{env}</code>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
          <p className="setup-note">On Handbook Cloud the Handbook team does this. Ask them to turn {guide.name} on.</p>
        </>
      )}

      <a className="gh-choose-back" href={back}>← Back to sources</a>
    </section>
  );
}

export default function ConnectSetupPage() {
  return (
    <main className="gh-choose">
      <Suspense fallback={<p className="muted">Loading…</p>}>
        <SetupInner />
      </Suspense>
    </main>
  );
}
