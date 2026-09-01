/**
 * Decorative header scenes for admin/explore pages.
 * Soft UI + dimensional layering — SVG only, motion via CSS.
 */
export type PageSceneVariant = "sources" | "spaces" | "people" | "reports" | "model";

export function PageSceneArt({ variant }: { variant: PageSceneVariant }) {
  const label =
    variant === "sources"
      ? "Connected knowledge"
      : variant === "spaces"
        ? "Team rooms"
        : variant === "reports"
          ? "On a schedule"
          : variant === "model"
            ? "Your own model"
            : "Your people";

  return (
    <div className={`page-scene page-scene--${variant}`} aria-hidden>
      <svg className="page-scene-svg" viewBox="0 0 320 200" fill="none" role="presentation">
        <defs>
          <linearGradient id={`psMesh-${variant}`} x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#99f6e4" stopOpacity="0.5" />
            <stop offset="0.5" stopColor="#5eead4" stopOpacity="0.22" />
            <stop offset="1" stopColor="#0f766e" stopOpacity="0.12" />
          </linearGradient>
          <linearGradient id={`psPanel-${variant}`} x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#ffffff" />
            <stop offset="1" stopColor="#f0fdfa" />
          </linearGradient>
          <filter id={`psSoft-${variant}`} x="-25%" y="-25%" width="150%" height="150%">
            <feDropShadow dx="0" dy="10" stdDeviation="12" floodColor="#0f766e" floodOpacity="0.16" />
          </filter>
        </defs>

        <ellipse className="ps-blob ps-blob-a" cx="70" cy="55" rx="58" ry="42" fill={`url(#psMesh-${variant})`} />
        <ellipse className="ps-blob ps-blob-b" cx="260" cy="150" rx="64" ry="46" fill={`url(#psMesh-${variant})`} />

        {variant === "sources" && (
          <>
            <g className="ps-float ps-float-1" filter={`url(#psSoft-${variant})`}>
              <rect x="36" y="42" width="100" height="64" rx="14" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <rect x="50" y="56" width="28" height="28" rx="8" fill="#ccfbf1" />
              <rect x="88" y="60" width="34" height="7" rx="3.5" fill="#d0d5dd" />
              <rect x="88" y="74" width="24" height="6" rx="3" fill="#e4e7ec" />
            </g>
            <g className="ps-float ps-float-2" filter={`url(#psSoft-${variant})`}>
              <rect x="184" y="28" width="100" height="64" rx="14" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <rect x="198" y="42" width="28" height="28" rx="8" fill="#ecfdf5" />
              <rect x="236" y="46" width="34" height="7" rx="3.5" fill="#d0d5dd" />
              <rect x="236" y="60" width="24" height="6" rx="3" fill="#e4e7ec" />
            </g>
            <g className="ps-float ps-float-3" filter={`url(#psSoft-${variant})`}>
              <rect x="96" y="118" width="128" height="52" rx="14" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <circle cx="122" cy="144" r="10" fill="#14b8a6" />
              <rect x="142" y="136" width="60" height="7" rx="3.5" fill="#98a2b3" />
              <rect x="142" y="150" width="42" height="6" rx="3" fill="#d0d5dd" />
            </g>
            <path
              className="ps-arc"
              d="M136 74C148 92 156 108 160 124"
              stroke="#14b8a6"
              strokeWidth="1.5"
              strokeDasharray="4 5"
              opacity="0.5"
            />
            <path
              className="ps-arc ps-arc-delay"
              d="M184 74C172 92 166 108 160 124"
              stroke="#0f766e"
              strokeWidth="1.5"
              strokeDasharray="4 5"
              opacity="0.4"
            />
          </>
        )}

        {variant === "spaces" && (
          <>
            <g className="ps-float ps-float-1" filter={`url(#psSoft-${variant})`}>
              <rect x="48" y="48" width="88" height="88" rx="18" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <rect x="64" y="66" width="56" height="8" rx="4" fill="#0f766e" opacity="0.35" />
              <rect x="64" y="84" width="40" height="6" rx="3" fill="#d0d5dd" />
              <rect x="64" y="98" width="48" height="6" rx="3" fill="#e4e7ec" />
            </g>
            <g className="ps-float ps-float-2" filter={`url(#psSoft-${variant})`}>
              <rect x="168" y="36" width="100" height="72" rx="16" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <circle cx="196" cy="64" r="14" fill="#ccfbf1" />
              <rect x="220" y="56" width="32" height="7" rx="3.5" fill="#d0d5dd" />
              <rect x="220" y="70" width="24" height="6" rx="3" fill="#e4e7ec" />
            </g>
            <g className="ps-float ps-float-3" filter={`url(#psSoft-${variant})`}>
              <rect x="152" y="124" width="116" height="48" rx="14" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <rect x="168" y="140" width="20" height="20" rx="6" fill="#14b8a6" opacity="0.85" />
              <rect x="198" y="142" width="52" height="7" rx="3.5" fill="#98a2b3" />
              <rect x="198" y="156" width="36" height="6" rx="3" fill="#d0d5dd" />
            </g>
          </>
        )}

        {variant === "people" && (
          <>
            <g className="ps-float ps-float-1" filter={`url(#psSoft-${variant})`}>
              <circle cx="110" cy="88" r="36" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <circle cx="110" cy="78" r="12" fill="#ccfbf1" />
              <path d="M90 108c4-10 12-16 20-16s16 6 20 16" fill="#14b8a6" opacity="0.45" />
            </g>
            <g className="ps-float ps-float-2" filter={`url(#psSoft-${variant})`}>
              <circle cx="198" cy="72" r="28" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <circle cx="198" cy="64" r="9" fill="#ecfdf5" />
              <path d="M182 92c3-8 9-12 16-12s13 4 16 12" fill="#0f766e" opacity="0.35" />
            </g>
            <g className="ps-float ps-float-3" filter={`url(#psSoft-${variant})`}>
              <rect x="88" y="138" width="144" height="40" rx="14" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <rect x="106" y="152" width="72" height="8" rx="4" fill="#d0d5dd" />
              <rect x="190" y="150" width="28" height="12" rx="6" fill="#ccfbf1" />
            </g>
          </>
        )}
        {/* Reports: what a scheduled report IS — activity on the left,
            folded into an envelope, on a repeating cadence. Shares the panel
            gradient, soft shadow and float classes with the other scenes, so
            it reads as the same family without repeating any of their shapes. */}
        {variant === "reports" && (
          <>
            {/* Activity being summarised: a sparkline of what happened. */}
            <g className="ps-float ps-float-1" filter={`url(#psSoft-${variant})`}>
              <rect x="30" y="96" width="104" height="72" rx="14" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <rect x="44" y="140" width="10" height="16" rx="3" fill="#ccfbf1" />
              <rect x="60" y="128" width="10" height="28" rx="3" fill="#5eead4" opacity="0.75" />
              <rect x="76" y="118" width="10" height="38" rx="3" fill="#14b8a6" opacity="0.8" />
              <rect x="92" y="134" width="10" height="22" rx="3" fill="#5eead4" opacity="0.6" />
              <rect x="108" y="124" width="10" height="32" rx="3" fill="#0f766e" opacity="0.45" />
              <rect x="44" y="110" width="42" height="7" rx="3.5" fill="#d0d5dd" />
            </g>

            {/* The report itself, as a sealed envelope. */}
            <g className="ps-float ps-float-2" filter={`url(#psSoft-${variant})`}>
              <rect x="150" y="44" width="128" height="84" rx="14" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <path d="M150 58l64 42 64-42" stroke="#14b8a6" strokeWidth="2" opacity="0.55" fill="none" />
              <rect x="166" y="104" width="52" height="7" rx="3.5" fill="#d0d5dd" />
              <rect x="226" y="102" width="36" height="11" rx="5.5" fill="#ccfbf1" />
            </g>

            {/* Cadence: four ticks, the next one filled — weekly, repeating. */}
            <g className="ps-float ps-float-3" filter={`url(#psSoft-${variant})`}>
              <rect x="146" y="140" width="136" height="40" rx="13" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <circle cx="168" cy="160" r="7" fill="#14b8a6" opacity="0.85" />
              <circle cx="192" cy="160" r="6" fill="#5eead4" opacity="0.6" />
              <circle cx="214" cy="160" r="6" fill="#d0d5dd" />
              <circle cx="236" cy="160" r="6" fill="#e4e7ec" />
              <rect x="252" y="154" width="18" height="12" rx="6" fill="#ccfbf1" />
            </g>

            {/* Activity folded into the report — the one line that ties the
                two panels together, same dashed idiom as the sources arc. */}
            <path
              className="ps-arc"
              d="M134 120C158 116 168 108 176 98"
              stroke="#0f766e"
              strokeWidth="1.5"
              strokeDasharray="4 5"
              opacity="0.45"
              fill="none"
            />
          </>
        )}

        {/* Model: what bringing your own model IS — your key on the left,
            your model as a chip, and answers coming back. Shares the panel
            gradient, soft shadow, float classes and the dashed-arc idiom with
            the other scenes, but repeats none of their shapes: no stacked
            cards (sources), no squares (spaces), no avatars (people), no bars
            or envelope (reports). */}
        {variant === "model" && (
          <>
            {/* The key the admin brings. */}
            <g className="ps-float ps-float-1" filter={`url(#psSoft-${variant})`}>
              <rect x="26" y="70" width="96" height="60" rx="14" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <circle cx="54" cy="100" r="11" fill="none" stroke="#14b8a6" strokeWidth="3.5" />
              <path d="M65 100h30M88 100v8M78 100v6" stroke="#0f766e" strokeWidth="3.5" strokeLinecap="round" opacity="0.75" />
            </g>

            {/* The model itself, as a chip with a node lattice. */}
            <g className="ps-float ps-float-2" filter={`url(#psSoft-${variant})`}>
              <rect x="158" y="34" width="124" height="92" rx="18" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <rect x="182" y="58" width="76" height="44" rx="12" fill="#ecfdf5" stroke="#99f6e4" />
              <circle cx="200" cy="72" r="5" fill="#14b8a6" />
              <circle cx="220" cy="88" r="5" fill="#0f766e" opacity="0.7" />
              <circle cx="240" cy="70" r="5" fill="#5eead4" />
              <path d="M200 72l20 16 20-18" stroke="#14b8a6" strokeWidth="1.6" fill="none" opacity="0.6" />
              {/* chip pins, the one shape no other scene uses */}
              <path d="M182 48v10M206 44v14M234 44v14M258 48v10" stroke="#d0d5dd" strokeWidth="3" strokeLinecap="round" />
              <path d="M182 102v10M206 102v14M234 102v14M258 102v10" stroke="#d0d5dd" strokeWidth="3" strokeLinecap="round" />
            </g>

            {/* The answer coming back to the team. */}
            <g className="ps-float ps-float-3" filter={`url(#psSoft-${variant})`}>
              <rect x="120" y="140" width="140" height="42" rx="16" fill={`url(#psPanel-${variant})`} stroke="#e4e7ec" />
              <rect x="138" y="154" width="64" height="7" rx="3.5" fill="#98a2b3" />
              <rect x="138" y="167" width="40" height="6" rx="3" fill="#d0d5dd" />
              <rect x="214" y="152" width="30" height="12" rx="6" fill="#ccfbf1" />
            </g>

            {/* key -> model, and model -> answer. */}
            <path
              className="ps-arc"
              d="M122 96C140 92 148 84 156 78"
              stroke="#14b8a6"
              strokeWidth="1.5"
              strokeDasharray="4 5"
              opacity="0.55"
              fill="none"
            />
            <path
              className="ps-arc ps-arc-delay"
              d="M216 128C212 136 204 140 196 141"
              stroke="#0f766e"
              strokeWidth="1.5"
              strokeDasharray="4 5"
              opacity="0.4"
              fill="none"
            />
          </>
        )}
      </svg>
      <span className="page-scene-badge">{label}</span>
    </div>
  );
}
