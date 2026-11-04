"use client";

/**
 * Setting up the company's own Google or Slack app, step by step.
 *
 * Handbook has no shared Google or Slack app: ours would put Google sign-in
 * behind a test-user list and slow Slack history to a crawl. Every value the
 * admin must type into Google or Slack is shown here with a Copy button, so
 * the setup is copying, not knowing. The full guide with pictures is
 * docs/handbook/connect-google-and-slack.md.
 */

import { useState } from "react";

import { BrandGlyph } from "./BrandGlyph";
import { api, ApiError, ConnectorApp } from "@/lib/api";

function CopyLine({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="own-app-copy">
      <code>{value}</code>
      <button
        className="button button-secondary button-sm"
        type="button"
        onClick={() => {
          void navigator.clipboard.writeText(value).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          });
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function GoogleSteps({ app }: { app: ConnectorApp }) {
  return (
    <ol className="own-app-steps">
      <li>
        Open{" "}
        <a href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noreferrer">
          Google Cloud
        </a>{" "}
        signed in with your company Google account, and create a project named <b>Handbook</b>.
      </li>
      <li>
        Turn on these four APIs. Open each link, check the project is <b>Handbook</b>, and press{" "}
        <b>Enable</b>:{" "}
        <a href="https://console.cloud.google.com/apis/library/drive.googleapis.com" target="_blank" rel="noreferrer">Drive</a>,{" "}
        <a href="https://console.cloud.google.com/apis/library/docs.googleapis.com" target="_blank" rel="noreferrer">Docs</a>,{" "}
        <a href="https://console.cloud.google.com/apis/library/sheets.googleapis.com" target="_blank" rel="noreferrer">Sheets</a>,{" "}
        <a href="https://console.cloud.google.com/apis/library/forms.googleapis.com" target="_blank" rel="noreferrer">Forms</a>.
      </li>
      <li>
        Open{" "}
        <a href="https://console.cloud.google.com/auth/overview" target="_blank" rel="noreferrer">
          Google Auth Platform
        </a>{" "}
        and press <b>Get started</b>. App name: <b>Handbook</b>. Your email as the support email.
        For <b>Audience</b> choose <b>Internal</b>. Then finish.
      </li>
      <li>
        Go to <b>Clients</b> → <b>Create client</b>. Application type: <b>Web application</b>. Name:{" "}
        <b>Handbook</b>. Under <b>Authorized redirect URIs</b>, add each of these:
        {app.redirect_uris.map((uri) => (
          <CopyLine key={uri} value={uri} />
        ))}
        Press <b>Create</b>.
      </li>
      <li>
        Google shows a <b>Client ID</b> and a <b>Client secret</b>. Paste both below and press{" "}
        <b>Save</b>.
      </li>
    </ol>
  );
}

function SlackSteps({ app }: { app: ConnectorApp }) {
  const [showManifest, setShowManifest] = useState(false);
  return (
    <ol className="own-app-steps">
      <li>
        Press{" "}
        <a href={app.create_url} target="_blank" rel="noreferrer">
          <b>Create the Slack app</b>
        </a>
        . Slack opens with everything filled in. Pick your workspace, press <b>Next</b>, then{" "}
        <b>Create</b>.{" "}
        <button className="button button-secondary button-sm" type="button" onClick={() => setShowManifest((v) => !v)}>
          {showManifest ? "Hide template" : "Slack shows an empty box?"}
        </button>
        {showManifest && app.manifest && (
          <>
            <p className="muted">
              Choose <b>From a manifest</b>, pick your workspace, switch to <b>JSON</b>, and paste this:
            </p>
            <CopyLine value={app.manifest} />
          </>
        )}
      </li>
      <li>
        If Slack says the Request URL did not answer, that is expected: Handbook doesn&apos;t have the
        app&apos;s keys yet. Press <b>Go to App Settings</b>.
      </li>
      <li>
        Under <b>Basic Information</b> → <b>App Credentials</b>, copy the <b>Client ID</b>,{" "}
        <b>Client Secret</b> and <b>Signing Secret</b> into the boxes below and press <b>Save</b>.
      </li>
      <li>
        Back in Slack, open <b>Event Subscriptions</b> and press <b>Retry</b> next to the Request URL.
        It shows <b>Verified</b>.
      </li>
    </ol>
  );
}

export function OwnAppSetup({
  provider,
  app,
  onSaved,
}: {
  provider: "google" | "slack";
  app: ConnectorApp;
  onSaved: (app: ConnectorApp) => void;
}) {
  const [open, setOpen] = useState(!app.configured && !app.server_app);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const name = provider === "google" ? "Google" : "Slack";
  const ready = app.configured || app.server_app;
  const canSave =
    clientId.trim() && clientSecret.trim() && (provider === "google" || signingSecret.trim());

  return (
    <div className={`card source-row${ready ? " is-linked" : ""}`}>
      <div className="source-row-head">
        <div className="source-row-main">
          <span className="source-row-mark">
            <BrandGlyph name={provider} size={28} />
          </span>
          <div className="source-row-copy">
            <h3>Your company&apos;s {name} app</h3>
            <p className="source-row-kind">
              {app.configured
                ? `Set up · Client ID ${app.client_id}`
                : app.server_app
                  ? "Using this server's app"
                  : "Not set up yet. Needed before you connect " + (provider === "google" ? "Google Drive" : "Slack")}
            </p>
          </div>
        </div>
        <div className="source-row-actions">
          <button className="button button-secondary" type="button" onClick={() => setOpen((v) => !v)}>
            {open ? "Hide" : app.configured ? "Change" : `Set up your ${name} app`}
          </button>
        </div>
      </div>

      {open && (
        <div className="stack" style={{ marginTop: "0.9rem" }}>
          <p className="muted" style={{ margin: 0 }}>
            {provider === "google"
              ? "About 10 minutes, once. You need a Google Workspace account. The app stays inside your company, so Google needs no review and your people can connect without being added one by one."
              : "About 5 minutes, once. You need permission to install apps in your Slack workspace. Your own app reads Slack at full speed."}
          </p>
          {provider === "google" ? <GoogleSteps app={app} /> : <SlackSteps app={app} />}
          <form
            className="stack"
            onSubmit={(e) => {
              e.preventDefault();
              setBusy(true);
              setError(null);
              api
                .saveConnectorApp(provider, {
                  client_id: clientId,
                  client_secret: clientSecret,
                  signing_secret: provider === "slack" ? signingSecret : null,
                })
                .then((updated) => {
                  setClientSecret("");
                  setSigningSecret("");
                  setSaved(true);
                  onSaved(updated);
                })
                .catch((err) => setError(err instanceof ApiError ? err.message : "Could not save. Try again."))
                .finally(() => setBusy(false));
            }}
          >
            <input className="input" aria-label="Client ID" placeholder="Client ID" value={clientId}
              onChange={(e) => setClientId(e.target.value)} autoComplete="off" />
            <input className="input" aria-label="Client Secret" placeholder="Client Secret" type="password"
              value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} autoComplete="off" />
            {provider === "slack" && (
              <input className="input" aria-label="Signing Secret" placeholder="Signing Secret" type="password"
                value={signingSecret} onChange={(e) => setSigningSecret(e.target.value)} autoComplete="off" />
            )}
            <div>
              <button className="button" type="submit" disabled={busy || !canSave}>
                {busy ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
          {error && <div className="banner banner-warn">{error}</div>}
          {saved && (
            <div className="banner banner-ok" role="status">
              Saved. Now press <b>Connect</b> on the {provider === "google" ? "Google Drive" : "Slack"} card.
              {app.configured ? " If it was already connected, connect it again so the new app is used." : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
