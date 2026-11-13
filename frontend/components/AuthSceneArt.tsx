/** Left half of sign-in and sign-up: what Handbook does and what happens
 *  next, about as tall as the form. */
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

  // What happens next, as one compact row (the step you are on is highlighted).
  const steps: Array<[string, string]> = isSignup
    ? [
        ["Request", "Company and email"],
        ["We review", "A person reads it"],
        ["Connect", "Your tools"],
        ["Invite", "Your team"],
      ]
    : [
        ["Your email", "Work address"],
        ["Open the link", "Works once"],
        ["You’re in", "Ask away"],
      ];
  const now = 0;

  return (
    <div className="auth-scene2">
      <p className="site-label">{isSignup ? "Request access" : "Sign in"}</p>
      <h1 className="sec-title">{title}</h1>
      <p className="sec-lead">{blurb}</p>
      <ul className="sec-checks">
        {points.map((line) => <li key={line}>{line}</li>)}
      </ul>

      <ol className="auth-steps" aria-label="What happens next" style={{ ["--cols" as string]: steps.length }}>
        {steps.map(([title, hint], i) => (
          <li key={title} className={i === now ? "is-now" : i < now ? "is-done" : ""}>
            <span className="auth-steps-n">{i + 1}</span>
            <b>{title}</b>
            <small>{hint}</small>
          </li>
        ))}
      </ol>
    </div>
  );
}
