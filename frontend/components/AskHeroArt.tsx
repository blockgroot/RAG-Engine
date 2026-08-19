/**
 * Decorative Ask empty-state scene — Soft UI / dimensional layering.
 * Pure SVG (no emoji). Motion lives in CSS so prefers-reduced-motion can kill it.
 */
export function AskHeroArt({ variant = "policy" }: { variant?: "policy" | "code" | "space" }) {
  const label =
    variant === "code" ? "Live GitHub" : variant === "space" ? "This space" : "Your documents";

  return (
    <div className={`ask-hero-art ask-hero-art--${variant}`} aria-hidden>
      <svg className="ask-hero-svg" viewBox="0 0 420 280" fill="none" role="presentation">
        <defs>
          <linearGradient id="askMesh" x1="40" y1="20" x2="380" y2="260" gradientUnits="userSpaceOnUse">
            <stop stopColor="#99f6e4" stopOpacity="0.55" />
            <stop offset="0.45" stopColor="#5eead4" stopOpacity="0.25" />
            <stop offset="1" stopColor="#0f766e" stopOpacity="0.12" />
          </linearGradient>
          <linearGradient id="askPanel" x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#ffffff" />
            <stop offset="1" stopColor="#f0fdfa" />
          </linearGradient>
          <filter id="askSoft" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="0" dy="12" stdDeviation="14" floodColor="#0f766e" floodOpacity="0.18" />
          </filter>
        </defs>

        {/* ambient blobs */}
        <ellipse className="ask-blob ask-blob-a" cx="90" cy="70" rx="70" ry="52" fill="url(#askMesh)" />
        <ellipse className="ask-blob ask-blob-b" cx="340" cy="200" rx="85" ry="60" fill="url(#askMesh)" />

        {/* floating panels */}
        <g className="ask-float ask-float-1" filter="url(#askSoft)">
          <rect x="36" y="48" width="118" height="72" rx="16" fill="url(#askPanel)" stroke="#e4e7ec" />
          <rect x="52" y="66" width="36" height="36" rx="10" fill="#ccfbf1" />
          <path
            d="M64 78h12M64 84h8M64 90h10"
            stroke="#0f766e"
            strokeWidth="2"
            strokeLinecap="round"
          />
          <rect x="98" y="70" width="40" height="8" rx="4" fill="#d0d5dd" />
          <rect x="98" y="86" width="28" height="6" rx="3" fill="#e4e7ec" />
        </g>

        <g className="ask-float ask-float-2" filter="url(#askSoft)">
          <rect x="268" y="36" width="118" height="72" rx="16" fill="url(#askPanel)" stroke="#e4e7ec" />
          <rect x="284" y="54" width="36" height="36" rx="10" fill="#ecfdf5" />
          <path
            d="M293 66v16M293 66h10a6 6 0 0 1 0 12h-6"
            stroke="#0d5c56"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <rect x="330" y="58" width="40" height="8" rx="4" fill="#d0d5dd" />
          <rect x="330" y="74" width="32" height="6" rx="3" fill="#e4e7ec" />
        </g>

        {/* central search orb */}
        <g className="ask-core" filter="url(#askSoft)">
          <circle cx="210" cy="148" r="54" fill="#0f766e" />
          <circle cx="210" cy="148" r="54" fill="url(#askMesh)" opacity="0.45" />
          <circle cx="210" cy="148" r="38" fill="#ffffff" opacity="0.18" />
          <circle cx="198" cy="136" r="16" stroke="#f0fdfa" strokeWidth="3.5" />
          <path
            d="M210 148l14 14"
            stroke="#f0fdfa"
            strokeWidth="3.5"
            strokeLinecap="round"
          />
        </g>

        {/* answer beam / result card */}
        <g className="ask-float ask-float-3" filter="url(#askSoft)">
          <rect x="118" y="198" width="184" height="56" rx="16" fill="url(#askPanel)" stroke="#e4e7ec" />
          <circle cx="142" cy="226" r="10" fill="#14b8a6" />
          <rect x="162" y="214" width="110" height="8" rx="4" fill="#98a2b3" />
          <rect x="162" y="230" width="78" height="6" rx="3" fill="#d0d5dd" />
        </g>

        {/* connection arcs */}
        <path
          className="ask-arc"
          d="M154 100C170 120 186 132 210 148"
          stroke="#14b8a6"
          strokeWidth="1.5"
          strokeDasharray="4 6"
          opacity="0.55"
        />
        <path
          className="ask-arc ask-arc-delay"
          d="M268 90C250 112 232 130 210 148"
          stroke="#0f766e"
          strokeWidth="1.5"
          strokeDasharray="4 6"
          opacity="0.45"
        />
      </svg>
      <span className="ask-hero-badge">{label}</span>
    </div>
  );
}
