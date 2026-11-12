"use client";

/**
 * Setting up the company's own Google or Slack app, one step at a time.
 *
 * Handbook has no shared Google or Slack app: ours would put Google sign-in
 * behind a test-user list and slow Slack history to a crawl. The admin doing
 * this is often not technical, and every step on screen at once read as a
 * wall, so the steps are a numbered list with only the current one open, its
 * link as a button, and every value to type into Google or Slack behind a
 * Copy button. The full guide is docs/handbook/connect-google-and-slack.md.
 */

import { ReactNode, useState } from "react";

import { BrandGlyph } from "./BrandGlyph";
import { api, ApiError, ConnectorApp } from "@/lib/api";

// ``extra`` follows the step's link: what to copy once that page is open.
type Step = {
  title: string;
  body: ReactNode;
  links?: { href: string; label: string }[];
  extra?: ReactNode;
  form?: boolean;
};

export function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="own-app-copy">
      <span className="own-app-copy-label">{label}</span>
      <div className="own-app-copy-box">
        <code>{value}</code>
        <button
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
      title: "Keep the app inside your company",
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
        <p>
          Press <b>Create client</b>, choose <b>Web application</b>, and name it <b>Handbook</b>. Under{" "}
          <b>Authorized redirect URIs</b>, add both addresses below, then press <b>Create</b>.
        </p>
      ),
      links: [{ href: "https://console.cloud.google.com/auth/clients", label: "Open Clients" }],
      extra: app.redirect_uris.map((uri, i) => <CopyField key={uri} label={`Address ${i + 1}`} value={uri} />),
    },
    {
      title: "Paste the keys into Handbook",
      body: <p>Google now shows a Client ID and a Client secret. Copy them here.</p>,
      form: true,
    },
  ];
}

function slackSteps(app: ConnectorApp, fallback: ReactNode): Step[] {
  return [
    {
      title: "Create the app in Slack",
      body: (
        <p>
          Slack opens with everything filled in. Pick your workspace, then press <b>Next</b> and <b>Create</b>.
        </p>
      ),
      links: app.create_url ? [{ href: app.create_url, label: "Create the Slack app" }] : [],
      extra: (
        <>
          <p className="own-app-note">
            Slack may say the Request URL did not answer. That is expected until step 3.
          </p>
          {fallback}
        </>
      ),
    },
    {
      title: "Paste the keys into Handbook",
      body: (
        <p>
          In Slack, open <b>Basic Information</b> and scroll to <b>App Credentials</b>. Copy the three
          values here.
        </p>
      ),
      form: true,
    },
    {
      title: "Let Slack check it can reach Handbook",
      body: (
        <p>
          In Slack, open <b>Event Subscriptions</b> and press <b>Retry</b> next to the Request URL. It
          should say <b>Verified</b>.
        </p>
      ),
    },
  ];
}

const HINTS: Record<string, string> = {
  "google-client_id": "Ends in .apps.googleusercontent.com",
  "google-client_secret": "Starts with GOCSPX-",
  "slack-client_id": "Two numbers with a dot, like 1234.5678",
  "slack-client_secret": "32 letters and numbers",
  "slack-signing_secret": "The third value, also 32 letters and numbers",
};

export function OwnAppSetup({
  provider,
  app,
  onSaved,
  asSource = false,
}: {
  provider: "google" | "slack";
  app: ConnectorApp;
  onSaved: (app: ConnectorApp) => void;
  /** Shown in a list of sources (onboarding): named after the source, not the app. */
  asSource?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [showManifest, setShowManifest] = useState(false);
  const [values, setValues] = useState({ client_id: "", client_secret: "", signing_secret: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const name = provider === "google" ? "Google" : "Slack";
  const source = provider === "google" ? "Google Drive" : "Slack";
  const ready = app.configured || app.server_app;

  const fallback = (
    <div className="own-app-fallback">
      <button className="own-app-text-button" type="button" onClick={() => setShowManifest((v) => !v)}>
        {showManifest ? "Hide the template" : "Slack shows an empty box instead?"}
      </button>
      {showManifest && app.manifest && (
        <>
          <p className="own-app-note">
            Choose <b>From a manifest</b>, pick your workspace, switch to <b>JSON</b>, and paste this.
          </p>
          <CopyField label="Template" value={app.manifest} />
        </>
      )}
    </div>
  );
  const steps = provider === "google" ? googleSteps(app) : slackSteps(app, fallback);
  const last = step === steps.length - 1;
  const fields: [keyof typeof values, string, boolean][] = [
    ["client_id", "Client ID", false],
    ["client_secret", "Client Secret", true],
    ...(provider === "slack" ? [["signing_secret", "Signing Secret", true] as [keyof typeof values, string, boolean]] : []),
  ];
  const canSave = fields.every(([k]) => values[k].trim());

  const save = () => {
    setBusy(true);
    setError(null);
    api
      .saveConnectorApp(provider, {
        client_id: values.client_id,
        client_secret: values.client_secret,
        signing_secret: provider === "slack" ? values.signing_secret : null,
      })
      .then((updated) => {
        setValues((v) => ({ ...v, client_secret: "", signing_secret: "" }));
        onSaved(updated);
        if (last) setDone(true);
        else setStep(step + 1);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not save. Try again."))
      .finally(() => setBusy(false));
  };

  const nav = (primary: ReactNode) => (
    <div className="own-app-nav">
      {step > 0 ? (
        <button className="button button-secondary" type="button" onClick={() => setStep(step - 1)}>
          ← Back
        </button>
      ) : (
        <span />
      )}
      {primary}
    </div>
  );

  return (
    <div className={`card source-row own-app${ready ? " is-linked" : ""}${open ? " is-open" : ""}`}>
      <div className="source-row-head">
        <div className="source-row-main">
          <span className="source-row-mark">
            <BrandGlyph name={provider} size={28} />
          </span>
          <div className="source-row-copy">
            <h3>{asSource ? source : `${name} app`}</h3>
            <p className="source-row-kind">
              {asSource
                ? `Set up your company's ${name} app first, about ${provider === "google" ? 10 : 5} minutes.`
                : ready
                  ? `${source} connects through this app.`
                  : `Needed once before you connect ${source}. About ${provider === "google" ? 10 : 5} minutes.`}
            </p>
          </div>
        </div>
        <div className="source-row-actions">
          {!asSource && (
            <span className={`badge ${ready ? "badge-verified" : "badge-pending"}`}>
              {app.configured ? "Ready" : app.server_app ? "Ready · this server's app" : "Not set up"}
            </span>
          )}
          <button
            className={`button${open || ready ? " button-secondary" : ""}`}
            type="button"
            onClick={() => {
              setOpen((v) => !v);
              setDone(false);
              setError(null);
              // Changing keys starts at the boxes; the app itself already exists.
              setStep(app.configured ? steps.findIndex((s) => s.form) : 0);
            }}
          >
            {open ? "Close" : app.configured ? "Change keys" : asSource ? "Set up app" : "Set up"}
          </button>
        </div>
      </div>

      {open && done && (
        <div className="own-app-panel">
          <div className="banner banner-ok" role="status">
            {name} app saved. Now press <b>Connect</b> (or <b>Reconnect</b>) on the {source} card below.
          </div>
        </div>
      )}

      {open && !done && (
        <div className="own-app-panel">
        <ol className="own-app-stepper">
          {steps.map((s, i) => {
            const state = i < step ? "is-done" : i === step ? "is-current" : "is-next";
            return (
              <li key={s.title} className={state} aria-current={i === step ? "step" : undefined}>
                <button
                  type="button"
                  disabled={i >= step}
                  onClick={() => setStep(i)}
                  title={i < step ? `Back to: ${s.title}` : s.title}
                >
                  <span className="own-app-dot" aria-hidden>{i < step ? "✓" : i + 1}</span>
                  <span className="own-app-dot-label">{s.title}</span>
                </button>
              </li>
            );
          })}
        </ol>
        {steps.map((s, i) =>
          i !== step ? null : (
            <section key={s.title} className="own-app-current" aria-label={s.title}>
              <p className="own-app-current-eyebrow">
                Step {i + 1} of {steps.length}
              </p>
              <h4>{s.title}</h4>
              <div className="own-app-step-body">
                {s.body}

                {s.links && s.links.length > 0 && (
                  <div className="own-app-links">
                    {s.links.map((l) => (
                      <a key={l.href} className="button button-secondary" href={l.href} target="_blank" rel="noreferrer">
                        {l.label} ↗
                      </a>
                    ))}
                  </div>
                )}

                {s.extra}

                {s.form ? (
                  <form
                    className="own-app-form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      save();
                    }}
                  >
                    {fields.map(([key, label, secret]) => (
                      <div className="field" key={key}>
                        <label htmlFor={`${provider}-${key}`}>{label}</label>
                        <input
                          id={`${provider}-${key}`}
                          className="input"
                          type={secret ? "password" : "text"}
                          value={values[key]}
                          onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
                          autoComplete="off"
                          spellCheck={false}
                        />
                        <span className="own-app-hint">{HINTS[`${provider}-${key}`]}</span>
                      </div>
                    ))}
                    {error && <div className="banner banner-warn">{error}</div>}
                    {nav(
                      <button className="button" type="submit" disabled={busy || !canSave}>
                        {busy ? "Saving…" : "Save keys"}
                      </button>,
                    )}
                  </form>
                ) : (
                  nav(
                    <button className="button" type="button" onClick={() => (last ? setDone(true) : setStep(step + 1))}>
                      {last ? "Done" : "Next"}
                    </button>,
                  )
                )}
              </div>
            </section>
          ),
        )}
        </div>
      )}
    </div>
  );
}
