"use client";

/**
 * Setting up the company's own Google or Slack app, one step at a time.
 *
 * Handbook has no shared Google or Slack app: ours would put Google sign-in
 * behind a test-user list and slow Slack history to a crawl. The admin doing
 * this is often not technical, and every step on screen at once read as a
 * wall, so each step is shown alone, with its link as a button and every value
 * to type into Google or Slack behind a Copy button. The full guide is
 * docs/handbook/connect-google-and-slack.md.
 */

import { ReactNode, useState } from "react";

import { BrandGlyph } from "./BrandGlyph";
import { api, ApiError, ConnectorApp } from "@/lib/api";

type Step = { title: string; body: ReactNode; links?: { href: string; label: string }[]; form?: boolean };

function CopyLine({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="own-app-copy">
      {label && <span className="own-app-copy-label">{label}</span>}
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

function googleSteps(app: ConnectorApp): Step[] {
  return [
    {
      title: "Create a Google Cloud project",
      body: (
        <p>
          Sign in with your <b>company</b> Google account and create a project named <b>Handbook</b>.
        </p>
      ),
      links: [{ href: "https://console.cloud.google.com/projectcreate", label: "Open Google Cloud" }],
    },
    {
      title: "Turn on four Google services",
      body: (
        <p>
          Open each one, check the project at the top says <b>Handbook</b>, and press <b>Enable</b>.
        </p>
      ),
      links: [
        { href: "https://console.cloud.google.com/apis/library/drive.googleapis.com", label: "Drive" },
        { href: "https://console.cloud.google.com/apis/library/docs.googleapis.com", label: "Docs" },
        { href: "https://console.cloud.google.com/apis/library/sheets.googleapis.com", label: "Sheets" },
        { href: "https://console.cloud.google.com/apis/library/forms.googleapis.com", label: "Forms" },
      ],
    },
    {
      title: "Name the app and keep it inside your company",
      body: (
        <p>
          Press <b>Get started</b>. Name it <b>Handbook</b> and use your email for support. Under{" "}
          <b>Audience</b>, choose <b>Internal</b>, then finish.
        </p>
      ),
      links: [{ href: "https://console.cloud.google.com/auth/overview", label: "Open Google Auth Platform" }],
    },
    {
      title: "Create the sign-in key",
      body: (
        <>
          <p>
            Press <b>Create client</b>. Choose <b>Web application</b> and name it <b>Handbook</b>. Under{" "}
            <b>Authorized redirect URIs</b>, add both of these:
          </p>
          {app.redirect_uris.map((uri, i) => (
            <CopyLine key={uri} value={uri} label={`Address ${i + 1}`} />
          ))}
          <p>
            Then press <b>Create</b>.
          </p>
        </>
      ),
      links: [{ href: "https://console.cloud.google.com/auth/clients", label: "Open Clients" }],
    },
    {
      title: "Paste the keys here",
      body: (
        <p>
          Google now shows a <b>Client ID</b> and a <b>Client secret</b>. Copy them into the boxes below.
        </p>
      ),
      form: true,
    },
  ];
}

function slackSteps(app: ConnectorApp, manifest: ReactNode): Step[] {
  return [
    {
      title: "Create the app in Slack",
      body: (
        <>
          <p>
            Slack opens with everything filled in. Pick your workspace, then press <b>Next</b> and{" "}
            <b>Create</b>.
          </p>
          <p className="muted">
            If Slack warns that the Request URL did not answer, that is expected. Press{" "}
            <b>Go to App Settings</b> and come back here.
          </p>
          {manifest}
        </>
      ),
      links: app.create_url ? [{ href: app.create_url, label: "Create the Slack app" }] : [],
    },
    {
      title: "Paste the keys here",
      body: (
        <p>
          In Slack, open <b>Basic Information</b> and scroll to <b>App Credentials</b>. Copy the three
          values into the boxes below.
        </p>
      ),
      form: true,
    },
    {
      title: "Let Slack check it can reach Handbook",
      body: (
        <p>
          In Slack, open <b>Event Subscriptions</b> and press <b>Retry</b> next to the Request URL. It should
          say <b>Verified</b>.
        </p>
      ),
    },
  ];
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
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [showManifest, setShowManifest] = useState(false);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const name = provider === "google" ? "Google" : "Slack";
  const source = provider === "google" ? "Google Drive" : "Slack";
  const ready = app.configured || app.server_app;

  const manifest = (
    <div className="own-app-fallback">
      <button className="own-app-link-button" type="button" onClick={() => setShowManifest((v) => !v)}>
        {showManifest ? "Hide the template" : "Slack shows an empty box instead?"}
      </button>
      {showManifest && app.manifest && (
        <>
          <p className="muted">
            Choose <b>From a manifest</b>, pick your workspace, switch to <b>JSON</b>, and paste this:
          </p>
          <CopyLine value={app.manifest} />
        </>
      )}
    </div>
  );
  const steps = provider === "google" ? googleSteps(app) : slackSteps(app, manifest);
  const current = steps[step];
  const last = step === steps.length - 1;
  const canSave = clientId.trim() && clientSecret.trim() && (provider === "google" || signingSecret.trim());

  const save = () => {
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
        onSaved(updated);
        if (last) setDone(true);
        else setStep(step + 1);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not save. Try again."))
      .finally(() => setBusy(false));
  };

  return (
    <div className={`card source-row own-app${ready ? " is-linked" : ""}`}>
      <div className="source-row-head">
        <div className="source-row-main">
          <span className="source-row-mark">
            <BrandGlyph name={provider} size={28} />
          </span>
          <div className="source-row-copy">
            <h3>
              {name} app{" "}
              <span className={`badge ${ready ? "badge-verified" : "badge-pending"}`}>
                {app.configured ? "Ready" : app.server_app ? "Ready (this server's app)" : "Not set up"}
              </span>
            </h3>
            <p className="source-row-kind">
              {ready
                ? `${source} connects through this app.`
                : `Needed once before you connect ${source}. About ${provider === "google" ? 10 : 5} minutes.`}
            </p>
          </div>
        </div>
        <div className="source-row-actions">
          <button
            className={`button${open || ready ? " button-secondary" : ""}`}
            type="button"
            onClick={() => {
              setOpen((v) => !v);
              setDone(false);
              // Changing keys starts at the boxes; the app itself already exists.
              setStep(app.configured ? steps.findIndex((s) => s.form) : 0);
            }}
          >
            {open ? "Close" : app.configured ? "Change keys" : "Set up"}
          </button>
        </div>
      </div>

      {open && done && (
        <div className="own-app-panel">
          <div className="banner banner-ok" role="status">
            {name} app saved. Now press <b>{app.configured ? "Connect or Reconnect" : "Connect"}</b> on the{" "}
            {source} card below.
          </div>
        </div>
      )}

      {open && !done && (
        <div className="own-app-panel">
          <div className="own-app-progress" aria-label={`Step ${step + 1} of ${steps.length}`}>
            {steps.map((s, i) => (
              <span key={s.title} className={i === step ? "is-current" : i < step ? "is-done" : ""} />
            ))}
            <span className="muted">
              Step {step + 1} of {steps.length}
            </span>
          </div>

          <h4 className="own-app-step-title">{current.title}</h4>
          <div className="own-app-step-body">{current.body}</div>

          {current.links && current.links.length > 0 && (
            <div className="own-app-links">
              {current.links.map((l) => (
                <a key={l.href} className="button button-secondary" href={l.href} target="_blank" rel="noreferrer">
                  {l.label} ↗
                </a>
              ))}
            </div>
          )}

          {current.form && (
            <form
              className="own-app-form"
              onSubmit={(e) => {
                e.preventDefault();
                save();
              }}
            >
              <div className="field">
                <label htmlFor={`${provider}-client-id`}>Client ID</label>
                <input id={`${provider}-client-id`} className="input" value={clientId}
                  onChange={(e) => setClientId(e.target.value)} autoComplete="off" />
              </div>
              <div className="field">
                <label htmlFor={`${provider}-client-secret`}>Client Secret</label>
                <input id={`${provider}-client-secret`} className="input" type="password" value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)} autoComplete="off" />
              </div>
              {provider === "slack" && (
                <div className="field">
                  <label htmlFor="slack-signing-secret">Signing Secret</label>
                  <input id="slack-signing-secret" className="input" type="password" value={signingSecret}
                    onChange={(e) => setSigningSecret(e.target.value)} autoComplete="off" />
                </div>
              )}
              {error && <div className="banner banner-warn">{error}</div>}
              <div className="own-app-nav">
                {step > 0 ? (
                  <button className="button button-secondary" type="button" onClick={() => setStep(step - 1)}>
                    Back
                  </button>
                ) : <span />}
                <button className="button" type="submit" disabled={busy || !canSave}>
                  {busy ? "Saving…" : "Save"}
                </button>
              </div>
            </form>
          )}

          {!current.form && (
            <div className="own-app-nav">
              {step > 0 ? (
                <button className="button button-secondary" type="button" onClick={() => setStep(step - 1)}>
                  Back
                </button>
              ) : <span />}
              <button
                className="button"
                type="button"
                onClick={() => (last ? setDone(true) : setStep(step + 1))}
              >
                {last ? "Done" : "Next"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
