/**
 * Landing product preview — dimensional layered UI mock (SVG, not a photo).
 * Gives the hero a real visual anchor instead of type-only atmosphere.
 */
export function LandingProductArt() {
  return (
    <div className="landing-product" aria-hidden>
      <div className="landing-product-glow" />
      <svg className="landing-product-svg" viewBox="0 0 640 420" fill="none" role="presentation">
        <defs>
          <linearGradient id="lpGlass" x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#ffffff" stopOpacity="0.96" />
            <stop offset="1" stopColor="#f0fdfa" stopOpacity="0.92" />
          </linearGradient>
          <linearGradient id="lpAccent" x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#2dd4bf" />
            <stop offset="1" stopColor="#0f766e" />
          </linearGradient>
          <filter id="lpShadow" x="-10%" y="-10%" width="130%" height="140%">
            <feDropShadow dx="0" dy="24" stdDeviation="28" floodColor="#0f172a" floodOpacity="0.14" />
          </filter>
        </defs>

        {/* window chrome */}
        <g filter="url(#lpShadow)">
          <rect x="48" y="36" width="544" height="348" rx="28" fill="url(#lpGlass)" stroke="#e4e7ec" />
          <rect x="48" y="36" width="148" height="348" rx="28" fill="#f8fafc" />
          <rect x="196" y="36" width="1" height="348" fill="#e4e7ec" />

          {/* rail marks */}
          <circle cx="84" cy="72" r="14" fill="url(#lpAccent)" />
          <rect x="108" y="64" width="56" height="10" rx="5" fill="#101828" opacity="0.85" />
          <rect x="72" y="112" width="100" height="36" rx="12" fill="#ccfbf1" />
          <rect x="84" y="124" width="48" height="8" rx="4" fill="#0f766e" />
          <rect x="72" y="160" width="100" height="28" rx="10" fill="#ffffff" stroke="#e4e7ec" />
          <rect x="72" y="198" width="100" height="28" rx="10" fill="#ffffff" stroke="#e4e7ec" />

          {/* main ask area */}
          <rect x="228" y="72" width="72" height="10" rx="5" fill="#98a2b3" />
          <rect x="228" y="98" width="200" height="28" rx="8" fill="#101828" opacity="0.9" />

          {/* floating suggestion cards */}
          <g className="lp-card lp-card-1">
            <rect x="228" y="150" width="168" height="54" rx="14" fill="#ffffff" stroke="#e4e7ec" />
            <rect x="244" y="166" width="20" height="20" rx="6" fill="#ccfbf1" />
            <rect x="274" y="168" width="96" height="8" rx="4" fill="#d0d5dd" />
            <rect x="274" y="182" width="64" height="6" rx="3" fill="#e4e7ec" />
          </g>
          <g className="lp-card lp-card-2">
            <rect x="408" y="150" width="148" height="54" rx="14" fill="#ffffff" stroke="#e4e7ec" />
            <rect x="424" y="166" width="20" height="20" rx="6" fill="#ecfdf5" />
            <rect x="454" y="168" width="80" height="8" rx="4" fill="#d0d5dd" />
            <rect x="454" y="182" width="52" height="6" rx="3" fill="#e4e7ec" />
          </g>

          {/* composer */}
          <rect x="228" y="300" width="328" height="48" rx="24" fill="#ffffff" stroke="#d0d5dd" />
          <circle cx="528" cy="324" r="16" fill="url(#lpAccent)" />
          <path d="M528 316v12M522 322l6-6 6 6" stroke="#f0fdfa" strokeWidth="2" strokeLinecap="round" />

          {/* answer card */}
          <g className="lp-card lp-card-3">
            <rect x="228" y="220" width="328" height="64" rx="16" fill="#ffffff" stroke="#e4e7ec" />
            <rect x="244" y="236" width="48" height="14" rx="7" fill="#ccfbf1" />
            <rect x="244" y="258" width="220" height="8" rx="4" fill="#e4e7ec" />
          </g>
        </g>
      </svg>
    </div>
  );
}
