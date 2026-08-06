/**
 * Auth left-panel scene — illustrated product story for login/signup.
 * Soft UI dimensional layering; motion via CSS.
 */
export function AuthSceneArt({ variant = "login" }: { variant?: "login" | "signup" }) {
  const title = variant === "signup" ? "Grounded answers for your team" : "Pick up where you left off";
  const blurb =
    variant === "signup"
      ? "Connect policies and code. Ask once — every answer cites its source."
      : "Your company’s knowledge, ready when you are — no password to remember.";

  return (
    <div className={`auth-scene auth-scene--${variant}`}>
      <div className="auth-scene-copy">
        <p className="auth-scene-kicker">Folio</p>
        <h1 className="auth-scene-title">{title}</h1>
        <p className="auth-scene-blurb">{blurb}</p>
        <ul className="auth-scene-points">
          <li>
            <span className="auth-scene-bullet" aria-hidden />
            Policies from Notion &amp; Drive
          </li>
          <li>
            <span className="auth-scene-bullet" aria-hidden />
            Live answers from GitHub
          </li>
          <li>
            <span className="auth-scene-bullet" aria-hidden />
            Citations on every reply
          </li>
        </ul>
      </div>

      <div className="auth-scene-art" aria-hidden>
        <svg className="auth-scene-svg" viewBox="0 0 420 300" fill="none" role="presentation">
          <defs>
            <linearGradient id="asMesh" x1="40" y1="20" x2="380" y2="280" gradientUnits="userSpaceOnUse">
              <stop stopColor="#99f6e4" stopOpacity="0.55" />
              <stop offset="0.5" stopColor="#5eead4" stopOpacity="0.22" />
              <stop offset="1" stopColor="#0f766e" stopOpacity="0.12" />
            </linearGradient>
            <linearGradient id="asPanel" x1="0" y1="0" x2="1" y2="1">
              <stop stopColor="#ffffff" />
              <stop offset="1" stopColor="#f0fdfa" />
            </linearGradient>
            <filter id="asSoft" x="-20%" y="-20%" width="140%" height="140%">
              <feDropShadow dx="0" dy="14" stdDeviation="16" floodColor="#0f766e" floodOpacity="0.18" />
            </filter>
          </defs>

          <ellipse className="as-blob as-blob-a" cx="90" cy="70" rx="72" ry="52" fill="url(#asMesh)" />
          <ellipse className="as-blob as-blob-b" cx="340" cy="220" rx="80" ry="58" fill="url(#asMesh)" />

          <g className="as-float as-float-1" filter="url(#asSoft)">
            <rect x="48" y="56" width="130" height="78" rx="18" fill="url(#asPanel)" stroke="#e4e7ec" />
            <rect x="66" y="74" width="32" height="32" rx="10" fill="#ccfbf1" />
            <rect x="110" y="78" width="48" height="8" rx="4" fill="#d0d5dd" />
            <rect x="110" y="94" width="36" height="6" rx="3" fill="#e4e7ec" />
          </g>

          <g className="as-float as-float-2" filter="url(#asSoft)">
            <rect x="248" y="40" width="130" height="78" rx="18" fill="url(#asPanel)" stroke="#e4e7ec" />
            <rect x="266" y="58" width="32" height="32" rx="10" fill="#ecfdf5" />
            <path
              d="M274 70v16M274 70h10a6 6 0 0 1 0 12h-6"
              stroke="#0d5c56"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <rect x="310" y="62" width="48" height="8" rx="4" fill="#d0d5dd" />
            <rect x="310" y="78" width="36" height="6" rx="3" fill="#e4e7ec" />
          </g>

          <g className="as-core" filter="url(#asSoft)">
            <circle cx="210" cy="160" r="48" fill="#0f766e" />
            <circle cx="210" cy="160" r="48" fill="url(#asMesh)" opacity="0.4" />
            <circle cx="198" cy="148" r="14" stroke="#f0fdfa" strokeWidth="3.2" />
            <path d="M209 159l12 12" stroke="#f0fdfa" strokeWidth="3.2" strokeLinecap="round" />
          </g>

          <g className="as-float as-float-3" filter="url(#asSoft)">
            <rect x="120" y="214" width="180" height="56" rx="16" fill="url(#asPanel)" stroke="#e4e7ec" />
            <circle cx="144" cy="242" r="10" fill="#14b8a6" />
            <rect x="164" y="230" width="110" height="8" rx="4" fill="#98a2b3" />
            <rect x="164" y="246" width="72" height="6" rx="3" fill="#d0d5dd" />
          </g>

          <path
            className="as-arc"
            d="M178 118C188 132 198 144 210 160"
            stroke="#14b8a6"
            strokeWidth="1.5"
            strokeDasharray="4 6"
            opacity="0.5"
          />
          <path
            className="as-arc as-arc-delay"
            d="M248 100C236 120 222 140 210 160"
            stroke="#0f766e"
            strokeWidth="1.5"
            strokeDasharray="4 6"
            opacity="0.4"
          />
        </svg>
      </div>
    </div>
  );
}
