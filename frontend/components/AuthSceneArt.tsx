import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

const TOOLS: BrandName[] = ["notion", "drive", "slack", "linear", "github"];

/**
 * Left half of sign-in and sign-up. These two pages are where someone decides
 * whether to bother, so the copy says what Handbook does today, and the
 * figure shows what happens next, step by step, in the home page's style.
 */
export function AuthSceneArt({ variant = "login" }: { variant?: "login" | "signup" }) {
  const isSignup = variant === "signup";

  const title = isSignup ? "Set up Handbook for your company." : "Ask across everything your team connected.";
  const blurb = isSignup
    ? "Tell us who you are. We read every request ourselves, and once you’re approved you connect your tools and invite the team."
    : "Sign in with your work email. We’ll send a one-time link, so there’s no password to remember.";
  const points = isSignup
    ? [
        "Connect Notion, Drive, Slack, Linear or GitHub",
        "Invite your team, who sign in with a link",
        "Give a project its own space, with its own tools",
      ]
    : [
        "Answers from your documents, chats, tickets and code",
        "A chart when the answer is a number",
        "Reports emailed daily, weekly or monthly",
      ];

  return (
    <div className="auth-scene2">
      <p className="site-label">{isSignup ? "Request access" : "Sign in"}</p>
      <h1 className="sec-title">{title}</h1>
      <p className="sec-lead">{blurb}</p>
      <ul className="sec-checks">
        {points.map((line) => <li key={line}>{line}</li>)}
      </ul>

      <div className="site-frame access auth-flow" aria-hidden>
        {isSignup ? (
          <>
            <div className="access-node">
              <span className="access-step">1</span>
              <div>
                <b>You send a request</b>
                <span className="access-hint">Your company name and work email</span>
              </div>
            </div>
            <div className="access-node access-gate">
              <span className="access-step">2</span>
              <div>
                <b>We review it <em className="auth-flow-pill">In review</em></b>
                <span className="access-hint">A person reads every request</span>
              </div>
            </div>
            <div className="access-node">
              <span className="access-step">3</span>
              <div>
                <b>You connect your tools</b>
                <div className="access-tools">
                  {TOOLS.map((t) => <BrandGlyph key={t} name={t} size={16} />)}
                </div>
              </div>
            </div>
            <div className="access-node access-last">
              <span className="access-step">4</span>
              <div>
                <b>You invite your team</b>
                <span className="access-hint">They sign in with a link, no password</span>
              </div>
            </div>
          </>
        ) : (
          <>
            <div className="access-node">
              <span className="access-step">1</span>
              <div>
                <b>Enter your work email</b>
                <span className="access-hint">you@company.com</span>
              </div>
            </div>
            <div className="access-node access-gate">
              <span className="access-step">2</span>
              <div>
                <b>Open the link we email you</b>
                <span className="access-hint">It works once and expires shortly</span>
              </div>
            </div>
            <div className="access-node access-last">
              <span className="access-step">3</span>
              <div>
                <b>You&rsquo;re in</b>
                <span className="access-hint">Ask your first question</span>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
