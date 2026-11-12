import type { Metadata } from "next";
import Link from "next/link";
import { LandingShell } from "@/components/LandingShell";
import { ContactLink } from "@/components/site/ContactLink";
import { AccessFigure } from "@/components/site/AccessFigure";
import { Background } from "@/components/site/Background";
import { ControlPanel } from "@/components/site/ControlPanel";
import { OwnAppsFigure } from "@/components/site/OwnAppsFigure";
import { TrustList } from "@/components/site/TrustList";

export const metadata: Metadata = {
  title: "Security · Handbook",
  description: "Who sees what, what admins control, and how your tools connect to Handbook.",
};

/**
 * The detail an IT or security lead reads, on one page they can pass around.
 * The home page keeps a short teaser that links here. Same copy rule as the
 * home page: the outcome, never the machinery.
 */
export default function SecurityPage() {
  return (
    <LandingShell>
      <section className="site-wrap sec sec-page-top" aria-labelledby="security-page-title">
        <div className="sec-head">
          <p className="site-label">Security</p>
          <h1 id="security-page-title" className="sec-title sec-page-title">
            Your data, seen only by the people who can open it.
          </h1>
          <p className="sec-lead">
            How Handbook keeps each person to their own documents, what your admins decide, and how
            your tools connect.
          </p>
        </div>
      </section>

      <section id="access" className="site-wrap sec" aria-labelledby="security-title">
        <div className="sec-split">
          <AccessFigure />
          <div>
            <p className="site-label">Access and privacy</p>
            <h2 id="security-title" className="sec-title">Each person sees only what they can open.</h2>
            <p className="sec-lead">
              Handbook copies each tool&rsquo;s sharing along with the content and checks it for every
              person, before anything is searched. Two colleagues can ask the same question and get
              answers from different documents.
            </p>
            <TrustList />
          </div>
        </div>
      </section>

      <section id="control" className="site-wrap sec" aria-labelledby="control-title">
        <div className="sec-head">
          <p className="site-label">Under your control</p>
          <h2 id="control-title" className="sec-title">Admins set the boundaries.</h2>
          <p className="sec-lead">
            Choose the model, separate teams into spaces, and decide what Agent mode may do.
            Everything can be changed later.
          </p>
        </div>
        <ControlPanel />
      </section>

      <section id="own-apps" className="site-wrap sec" aria-labelledby="own-apps-title">
        <div className="oa">
          <div className="sec-head">
            <p className="site-label">Google and Slack</p>
            <h2 id="own-apps-title" className="sec-title">Connected through your own app.</h2>
            <p className="sec-lead">
              Your company creates its own Google and Slack app once, in about 10 and 5 minutes.
              Handbook walks you through each step, with every value ready to copy.
            </p>
            <ul className="oa-why">
              <li>Everyone in your company can sign in, with no Google review</li>
              <li>Slack is read at full speed</li>
              <li>Your data travels only between your company and Handbook</li>
            </ul>
          </div>
          <OwnAppsFigure />
        </div>
      </section>

      <section id="background" className="site-wrap sec" aria-labelledby="background-title">
        <div className="sec-head">
          <p className="site-label">In the background</p>
          <h2 id="background-title" className="sec-title">Keeps working after you ask.</h2>
          <p className="sec-lead">Handbook stays up to date on its own, and notices what people could not find.</p>
        </div>
        <Background />
      </section>

      <section className="site-wrap sec dep-close" aria-labelledby="security-close-title">
        <h2 id="security-close-title" className="sec-title">Want your data on your own servers?</h2>
        <p className="sec-lead">Self-Hosted runs the same Handbook inside your company.</p>
        <div className="dep-close-btns">
          <Link href="/deployment" className="site-btn site-btn-solid">See deployment options</Link>
          <Link href="/signup" className="site-btn site-btn-ghost">Request access</Link>
        </div>
        <p className="dep-close-contact">
          Questions first? <ContactLink />
        </p>
      </section>
    </LandingShell>
  );
}
