import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import { LandingShell } from "@/components/LandingShell";
import { ContactLink } from "@/components/site/ContactLink";
import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { DeployHero } from "@/components/site/DeployHero";
import { DeployChecklist } from "@/components/site/DeployChecklist";
import { CloudSetupDemo } from "@/components/site/CloudSetupDemo";
import { UpdateDemo } from "@/components/site/UpdateDemo";

export const metadata: Metadata = {
  title: "Deployment · Handbook",
  description: "Two ways to run Handbook: Handbook Cloud, hosted by us, or Self-Hosted on your own servers.",
};

/*
 * The two ways a company runs Handbook, in plain words. Every claim here
 * comes from docs/deployment-options.md and the self-hosted package
 * (deploy/selfhost/: install.sh, upgrade.sh, backup.sh, docs/). Public page:
 * no vendor, model or company names. Laid out open (rules, paths, a stack)
 * rather than as cards: the opening picture carries the idea.
 */


const TOOLS: BrandName[] = ["notion", "drive", "slack", "linear", "github"];

/** One clear sentence per cell; the words that matter are bold. */
const COMPARE: Array<[string, ReactNode, ReactNode]> = [
  ["Setup", <><b>We set it up for you.</b> Nothing to install.</>, <><b>Your IT team installs it</b> on your server, or we do it for you.</>],
  ["Running it", <><b>We run it</b>, so you never have to think about it.</>, <><b>Your IT team runs it.</b> We help on a support plan.</>],
  ["Your data", <>Stored <b>in our cloud</b>, kept separate from every other company.</>, <>Stored <b>on your own servers</b>. It never leaves your company.</>],
  ["Updates", <>New features <b>arrive automatically</b>.</>, <>You update <b>when you choose</b>, with one click.</>],
  ["AI model", <>Use <b>ours</b>, or connect your company&rsquo;s own.</>, <>Uses <b>your company&rsquo;s own</b> AI account.</>],
  ["Best for", <>Teams who want to <b>start today</b>.</>, <>Companies that must keep <b>data on their own servers</b>.</>],
];

/** How a release reaches a self-hosted install (release workflow + docs/UPGRADE.md). */
const RELEASE: Array<{ who: string; title: string; text: string; icon: string }> = [
  {
    who: "Step 1 · Our team",
    title: "We publish a new version",
    text: "Each release comes with notes that explain what changed and whether anything needs doing on your server before you update.",
    icon: "M21 8 12 3 3 8l9 5 9-5ZM3 8v8l9 5 9-5V8M12 13v8",
  },
  {
    who: "Step 2 · Your server",
    title: "Your Handbook notices it",
    text: "Every few hours your server checks our public list of releases. It only reads that list; nothing about your company or your data is sent to us.",
    icon: "M20 12a8 8 0 1 1-2.34-5.66M20 4v4h-4",
  },
  {
    who: "Step 3 · Your admins",
    title: "Your admins are told",
    text: "Admins see the new version in the notification bell and get an email about it. A release that fixes a security problem is marked urgent.",
    icon: "M6 9a6 6 0 1 1 12 0c0 3.2.7 4.9 1.5 5.9.4.5 0 1.1-.6 1.1H5.1c-.6 0-1-.6-.6-1.1C5.3 13.9 6 12.2 6 9ZM10 19a2 2 0 0 0 4 0",
  },
  {
    who: "Step 4 · One click",
    title: "An admin installs it",
    text: "One click on Update starts it. Handbook backs up your data first, and if anything goes wrong it switches back to the previous version on its own.",
    icon: "M12 3 4.5 6v5.5c0 4.6 3.2 8.4 7.5 9.5 4.3-1.1 7.5-4.9 7.5-9.5V6L12 3ZM8.5 12l2.5 2.5 4.5-5",
  },
];

const PROMISES = [
  "A daily backup of your database can be set up with one command.",
  "We support the current version and the one before it.",
  "Your named contact is told about every security fix.",
];

export default function DeploymentPage() {
  return (
    <LandingShell>
      <section className="site-wrap sec dep-top" aria-label="Deployment">
        <DeployHero />
      </section>

      {/* -- compare: open rows, two columns, a hairline between ------------- */}
      <section className="site-wrap sec" aria-labelledby="dep-compare-title">
        <div className="sec-head">
          <p className="site-label">Side by side</p>
          <h2 id="dep-compare-title" className="sec-title">Which one fits your company?</h2>
        </div>
        <dl className="dep-cmp">
          <div className="dep-cmp-head" aria-hidden>
            <span />
            <span className="is-cloud">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 9.5a4.25 4.25 0 0 1-.5 8.5z" /></svg>
              Cloud<small>We run it</small>
            </span>
            <span className="is-self">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M4 4h16v6H4zM4 14h16v6H4zM7.5 7h.01M7.5 17h.01" /></svg>
              Self-Hosted<small>You run it</small>
            </span>
          </div>
          {COMPARE.map(([topic, cloud, self]) => (
            <div key={topic} className="dep-cmp-row">
              <dt>{topic}</dt>
              <dd data-label="Cloud" className="is-cloud">{cloud}</dd>
              <dd data-label="Self-Hosted" className="is-self">{self}</dd>
            </div>
          ))}
        </dl>
      </section>

      {/* -- cloud: one path, three stops ------------------------------------ */}
      <section id="cloud" className="site-wrap sec" aria-labelledby="dep-cloud-title">
        <div className="dcloud">
          <div className="sec-head">
            <p className="site-label">Handbook Cloud</p>
            <h2 id="dep-cloud-title" className="sec-title">Your company is set up in three steps.</h2>
            <p className="sec-lead">
              Nothing to install. We host, back up and update Handbook, and fix problems when they happen.
              A person on our side reads every request.
            </p>
            <Link href="/signup" className="site-btn site-btn-solid dep-cta">Request access</Link>
          </div>
          <CloudSetupDemo />
        </div>
      </section>

      {/* -- self-hosted ----------------------------------------------------- */}
      <section id="self-hosted" className="site-wrap sec" aria-labelledby="dep-self-title">
        <div className="dself-top">
          <div className="sec-head">
            <p className="site-label">Handbook Self-Hosted</p>
            <h2 id="dep-self-title" className="sec-title">Everything you need to run it yourself.</h2>
            <p className="sec-lead">
              A ready-to-run package with an install guide. Your IT team installs
              it, or we install it for you on your server and hand access back
              when we are done.
            </p>
          </div>

          <div>
            <h3 className="dep-sub is-first">What you’ll need</h3>
            <DeployChecklist />
          </div>
        </div>

        <h3 className="dep-sub">The install, as your IT team sees it</h3>
        <div className="dterm-wrap">
          <div className="dterm" aria-label="Install commands">
            <div className="dterm-bar"><i /><i /><i /><span>handbook — server</span></div>
            <pre>
              <span className="c"># 1. Download the package and unzip it</span>{"\n"}
              <span className="p">$</span> unzip handbook-selfhost-1.0.0.zip &amp;&amp; cd handbook{"\n\n"}
              <span className="c"># 2. First run writes your settings file</span>{"\n"}
              <span className="p">$</span> ./install.sh{"\n"}
              <span className="o">✓ Created .env. Fill it in, then run ./install.sh again.</span>{"\n\n"}
              <span className="c"># 3. Add your keys and passwords</span>{"\n"}
              <span className="p">$</span> nano .env{"\n\n"}
              <span className="c"># 4. Second run starts Handbook</span>{"\n"}
              <span className="p">$</span> ./install.sh{"\n"}
              <span className="o">✓ Handbook is running</span>{"\n"}
              <span className="o">✓ Your company is created. Sign in at https://handbook.yourcompany.com</span>{"\n"}
              <span className="p">$</span> <span className="dterm-cursor" />
            </pre>
          </div>
          <ul className="dterm-notes">
            <li><b>Your keys stay with you.</b> The settings file is filled in by your own team. We never see it.</li>
            <li><b>Nothing missed.</b> The installer checks every setting and names anything left empty.</li>
            <li><b>Then connect your tools.</b> Our guide covers each one; Slack and GitHub come with ready-made app templates.</li>
          </ul>
        </div>

        <h3 className="dep-sub">What runs on your server</h3>
        <div className="darch" aria-label="What runs on your server">
          <div className="darch-frame"><span>Your server</span></div>
          <div className="darch-team">
            <div className="dh-people"><i className="is-a">P</i><i className="is-b">S</i><i className="is-c">L</i></div>
            <small>Your team</small>
          </div>
          <i className="darch-line is-team" />
          <div className="darch-node is-web"><b>Web address</b><small>Secure https</small></div>
          <i className="darch-line is-1" />
          <div className="darch-node is-app"><b>Handbook</b><small>Chat and admin pages</small></div>
          <i className="darch-line is-2" />
          <div className="darch-node is-db"><b>Database</b><small>Your data stays here</small></div>
          <i className="darch-vline" />
          <div className="darch-node is-sync"><b>Background sync</b><small>Keeps it up to date</small></div>
          <i className="darch-line is-tools" />
          <div className="darch-tools">
            <span className="dep-logos">{TOOLS.map((t) => <i key={t}><BrandGlyph name={t} size={12} /></i>)}</span>
            <small>Your tools</small>
          </div>
          <p className="darch-cap">Plus an updater for one-click updates, and scripts to install, upgrade and back up.</p>
        </div>

        <div className="dupd2-head">
          <h3 className="dep-sub">How updates reach you</h3>
          <p className="dupd2-lead">
            With Self-Hosted, nothing changes on your server until one of your admins chooses to
            update. Here is how a new version gets from us to you.
          </p>
        </div>
        <div className="dupd2">
          <ol className="dupd2-steps">
            {RELEASE.map((s) => (
              <li key={s.title} className="dupd2-step">
                <span className="dupd2-icon" aria-hidden>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={s.icon} /></svg>
                </span>
                <div>
                  <span className="dupd2-who">{s.who}</span>
                  <h4>{s.title}</h4>
                  <p>{s.text}</p>
                </div>
              </li>
            ))}
          </ol>
          <div className="dupd2-side">
          <div className="dupd2-promise">
            <p className="dupd2-promise-title">What you can count on</p>
            <ul>
              {PROMISES.map((p) => (
                <li key={p}>
                  <svg viewBox="0 0 20 20" fill="none" aria-hidden><path d="m5.5 10.5 3 3 6-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                  {p}
                </li>
              ))}
            </ul>
          </div>
          <div className="dupd2-preview">
            <p className="dupd2-promise-title">What an admin sees at step 4</p>
            <UpdateDemo />
          </div>
          </div>
        </div>
      </section>

      <section className="site-wrap sec dep-close">
        <h2 className="sec-title">Not sure which one?</h2>
        <p className="sec-lead">Start on Cloud today. If your company needs its data on its own servers, Self-Hosted runs the same Handbook.</p>
        <div className="dep-close-btns">
          <Link href="/signup" className="site-btn site-btn-solid">Request access</Link>
          <Link href="/" className="site-btn site-btn-ghost">Back to the home page</Link>
        </div>
        <p className="dep-close-contact">
          Questions first? <ContactLink />
        </p>
      </section>
    </LandingShell>
  );
}
