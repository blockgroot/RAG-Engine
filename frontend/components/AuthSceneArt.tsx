/**
 * Auth left-panel scenes — Sign in and Request access get *different*
 * illustrations (not just different copy on the same search graphic).
 */

function SharedDefs({ prefix }: { prefix: string }) {
  return (
    <defs>
      <linearGradient id={`${prefix}Mesh`} x1="40" y1="20" x2="380" y2="280" gradientUnits="userSpaceOnUse">
        <stop stopColor="#99f6e4" stopOpacity="0.55" />
        <stop offset="0.5" stopColor="#5eead4" stopOpacity="0.22" />
        <stop offset="1" stopColor="#0f766e" stopOpacity="0.12" />
      </linearGradient>
      <linearGradient id={`${prefix}Panel`} x1="0" y1="0" x2="1" y2="1">
        <stop stopColor="#ffffff" />
        <stop offset="1" stopColor="#f0fdfa" />
      </linearGradient>
      <filter id={`${prefix}Soft`} x="-20%" y="-20%" width="140%" height="140%">
        <feDropShadow dx="0" dy="14" stdDeviation="16" floodColor="#0f766e" floodOpacity="0.18" />
      </filter>
    </defs>
  );
}

/** Returning user: magic-link mail → unlock → desk ready. */
function LoginSceneSvg() {
  return (
    <svg className="auth-scene-svg" viewBox="0 0 420 300" fill="none" role="presentation">
      <SharedDefs prefix="login" />

      <ellipse className="as-blob as-blob-a" cx="70" cy="220" rx="68" ry="48" fill="url(#loginMesh)" />
      <ellipse className="as-blob as-blob-b" cx="350" cy="60" rx="74" ry="52" fill="url(#loginMesh)" />

      {/* Envelope with glowing magic link */}
      <g className="as-float as-float-1" filter="url(#loginSoft)">
        <rect x="42" y="78" width="150" height="110" rx="20" fill="url(#loginPanel)" stroke="#e4e7ec" />
        <path
          d="M62 108h110a12 12 0 0 1 12 12v46a12 12 0 0 1-12 12H62a12 12 0 0 1-12-12v-46a12 12 0 0 1 12-12Z"
          fill="#ccfbf1"
          stroke="#99f6e4"
          strokeWidth="1.5"
        />
        <path
          d="M50 120l67 42 67-42"
          stroke="#0f766e"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <rect x="78" y="168" width="78" height="14" rx="7" fill="#0f766e" />
        <rect x="90" y="172" width="54" height="6" rx="3" fill="#f0fdfa" opacity="0.9" />
      </g>

      {/* Unlock / keyless badge */}
      <g className="as-float as-float-2" filter="url(#loginSoft)">
        <rect x="248" y="48" width="128" height="88" rx="18" fill="url(#loginPanel)" stroke="#e4e7ec" />
        <circle cx="286" cy="92" r="22" fill="#ecfdf5" stroke="#99f6e4" strokeWidth="1.5" />
        <rect x="278" y="88" width="16" height="20" rx="4" fill="#0d5c56" />
        <circle cx="286" cy="84" r="7" stroke="#0d5c56" strokeWidth="2.2" fill="none" />
        <rect x="318" y="78" width="40" height="8" rx="4" fill="#d0d5dd" />
        <rect x="318" y="94" width="28" height="6" rx="3" fill="#e4e7ec" />
      </g>

      {/* Welcome-back desk / Ask ready */}
      <g className="as-float as-float-3" filter="url(#loginSoft)">
        <rect x="150" y="198" width="200" height="68" rx="18" fill="url(#loginPanel)" stroke="#e4e7ec" />
        <circle cx="178" cy="232" r="14" fill="#14b8a6" />
        <path
          d="M172 232l4 4 8-10"
          stroke="#f0fdfa"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <rect x="204" y="218" width="120" height="9" rx="4.5" fill="#98a2b3" />
        <rect x="204" y="236" width="86" height="7" rx="3.5" fill="#d0d5dd" />
      </g>

      <path
        className="as-arc"
        d="M192 150C210 170 230 190 250 210"
        stroke="#14b8a6"
        strokeWidth="1.5"
        strokeDasharray="4 6"
        opacity="0.5"
      />
      <path
        className="as-arc as-arc-delay"
        d="M286 136C270 160 240 190 210 220"
        stroke="#0f766e"
        strokeWidth="1.5"
        strokeDasharray="4 6"
        opacity="0.35"
      />

      <g className="as-pill" filter="url(#loginSoft)">
        <rect x="268" y="248" width="108" height="28" rx="14" fill="#fff" stroke="#0f766e" strokeWidth="1.4" />
        <text
          x="322"
          y="266"
          textAnchor="middle"
          fill="#0d5c56"
          fontSize="10"
          fontWeight="700"
          fontFamily="system-ui,sans-serif"
          letterSpacing="0.08em"
        >
          MAGIC LINK
        </text>
      </g>
    </svg>
  );
}

/** New org: request → review → sources waiting to connect. */
function SignupSceneSvg() {
  return (
    <svg className="auth-scene-svg" viewBox="0 0 420 300" fill="none" role="presentation">
      <SharedDefs prefix="signup" />

      <ellipse className="as-blob as-blob-a" cx="340" cy="70" rx="70" ry="50" fill="url(#signupMesh)" />
      <ellipse className="as-blob as-blob-b" cx="80" cy="230" rx="76" ry="54" fill="url(#signupMesh)" />

      {/* Company / org card */}
      <g className="as-float as-float-1" filter="url(#signupSoft)">
        <rect x="36" y="52" width="148" height="100" rx="18" fill="url(#signupPanel)" stroke="#e4e7ec" />
        <rect x="54" y="72" width="36" height="36" rx="10" fill="#ccfbf1" />
        <path
          d="M66 98V78h8v4h8v-4h8v20h-6v-6h-4v6h-6v-6h-4v6h-4Z"
          fill="#0f766e"
        />
        <rect x="102" y="78" width="62" height="8" rx="4" fill="#d0d5dd" />
        <rect x="102" y="94" width="46" height="6" rx="3" fill="#e4e7ec" />
        <rect x="54" y="122" width="112" height="10" rx="5" fill="#ecfdf5" stroke="#99f6e4" />
      </g>

      {/* Review queue — pending approval */}
      <g className="as-core as-core-signup" filter="url(#signupSoft)">
        <rect x="196" y="88" width="168" height="112" rx="20" fill="url(#signupPanel)" stroke="#e4e7ec" />
        <circle cx="228" cy="124" r="16" fill="#fef3c7" stroke="#f59e0b" strokeWidth="1.5" />
        <path
          d="M228 116v10M228 132.5h.01"
          stroke="#b45309"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
        <rect x="256" y="112" width="88" height="8" rx="4" fill="#d0d5dd" />
        <rect x="256" y="128" width="64" height="6" rx="3" fill="#e4e7ec" />
        <rect x="214" y="152" width="132" height="12" rx="6" fill="#fff7ed" stroke="#fdba74" />
        <rect x="214" y="172" width="100" height="10" rx="5" fill="#ecfdf5" stroke="#99f6e4" />
      </g>

      {/* Connect sources waiting — Notion / Drive / GitHub marks */}
      <g className="as-float as-float-3" filter="url(#signupSoft)">
        <rect x="88" y="208" width="244" height="64" rx="18" fill="url(#signupPanel)" stroke="#e4e7ec" />
        <rect x="108" y="224" width="28" height="28" rx="8" fill="#111827" />
        <rect x="114" y="230" width="16" height="16" rx="2" fill="#fff" opacity="0.9" />
        <rect x="148" y="224" width="28" height="28" rx="8" fill="#eff6ff" stroke="#bfdbfe" />
        <circle cx="155" cy="231" r="3.5" fill="#ea4335" />
        <circle cx="163" cy="231" r="3.5" fill="#fbbc04" />
        <circle cx="155" cy="239" r="3.5" fill="#34a853" />
        <circle cx="163" cy="239" r="3.5" fill="#4285f4" />
        <rect x="188" y="224" width="28" height="28" rx="8" fill="#f3f4f6" stroke="#d0d5dd" />
        <path
          d="M196 236c0 4 3 8 6 9v-4c-1-.4-2-2-2-4 0-2 1-3 2-3.5V232c-3 .8-6 3-6 4Z"
          fill="#24292f"
        />
        <rect x="232" y="228" width="80" height="8" rx="4" fill="#98a2b3" />
        <rect x="232" y="244" width="56" height="6" rx="3" fill="#d0d5dd" />
      </g>

      <path
        className="as-arc"
        d="M184 120C200 140 210 150 220 160"
        stroke="#f59e0b"
        strokeWidth="1.5"
        strokeDasharray="4 6"
        opacity="0.55"
      />
      <path
        className="as-arc as-arc-delay"
        d="M280 200C250 210 220 220 190 230"
        stroke="#0f766e"
        strokeWidth="1.5"
        strokeDasharray="4 6"
        opacity="0.4"
      />

      <g className="as-pill" filter="url(#signupSoft)">
        <rect x="286" y="248" width="100" height="28" rx="14" fill="#fff" stroke="#b45309" strokeWidth="1.4" />
        <text
          x="336"
          y="266"
          textAnchor="middle"
          fill="#92400e"
          fontSize="10"
          fontWeight="700"
          fontFamily="system-ui,sans-serif"
          letterSpacing="0.08em"
        >
          IN REVIEW
        </text>
      </g>
    </svg>
  );
}

export function AuthSceneArt({ variant = "login" }: { variant?: "login" | "signup" }) {
  const isSignup = variant === "signup";

  // "Handbook" is a common noun, so "bring your company into Handbook" reads as
  // a typo. Possessive phrasing makes the name work as a product name instead.
  const title = isSignup ? "Set up your company's Handbook" : "Welcome back — no password needed";
  const blurb = isSignup
    ? "Tell us who you are. We review each request, then you connect sources and invite your team."
    : "Enter your work email and we’ll send a one-time sign-in link. Secure, fast, forgettable.";

  const points = isSignup
    ? ["Request reviewed by a human", "Connect Notion, Drive, or GitHub", "Invite teammates when you’re ready"]
    : ["One-time magic link to your inbox", "Pick up Ask, Spaces, and Sources", "Same grounded answers as last time"];

  return (
    <div className={`auth-scene auth-scene--${variant}`}>
      <div className="auth-scene-copy">
        <p className="auth-scene-kicker">Handbook</p>
        <h1 className="auth-scene-title">{title}</h1>
        <p className="auth-scene-blurb">{blurb}</p>
        <ul className="auth-scene-points">
          {points.map((line) => (
            <li key={line}>
              <span className="auth-scene-bullet" aria-hidden />
              {line}
            </li>
          ))}
        </ul>
      </div>

      <div className="auth-scene-art" aria-hidden>
        {isSignup ? <SignupSceneSvg /> : <LoginSceneSvg />}
      </div>
    </div>
  );
}
