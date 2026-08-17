/** Small brand-neutral provider marks — SVG only, no emoji. */

export function ProviderMark({
  provider,
  size = 28,
}: {
  provider: "notion" | "google" | "github" | "slack";
  size?: number;
}) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none" as const,
    "aria-hidden": true as const,
  };

  if (provider === "notion") {
    return (
      <span className="provider-mark provider-mark-notion">
        <svg {...common}>
          <rect x="4" y="3" width="16" height="18" rx="2.5" stroke="currentColor" strokeWidth="1.75" />
          <path d="M8 8h8M8 12h5M8 16h6" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
        </svg>
      </span>
    );
  }
  if (provider === "google") {
    return (
      <span className="provider-mark provider-mark-google">
        <svg {...common}>
          <path
            d="M4 8.5h16v9.5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8.5Z"
            stroke="currentColor"
            strokeWidth="1.75"
          />
          <path d="M4 8.5 12 3l8 5.5" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round" />
        </svg>
      </span>
    );
  }
  if (provider === "github") {
    return (
      <span className="provider-mark provider-mark-github">
        <svg {...common}>
          <path
            d="M9 19c-4 1.5-4-2-6-2m12 4v-3.5c0-1 .3-1.7 1-2.3 3.2-.4 4-1.8 4-4.2 0-1-.3-1.8-.9-2.4.1-.4.4-1.6-.1-2.9 0 0-.8-.3-2.8 1a9 9 0 0 0-5 0C8.8 4.5 8 4.8 8 4.8c-.5 1.3-.2 2.5-.1 2.9A3.7 3.7 0 0 0 7 10c0 2.4.8 3.8 4 4.2.6.5 1 1.3 1 2.3V21"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
    );
  }
  // Slack: a brand-neutral hash/channel glyph (not the actual multi-color
  // Slack logo, same "geometric, not brand marks" convention as the others).
  return (
    <span className="provider-mark provider-mark-slack">
      <svg {...common}>
        <path
          d="M9 4v16M15 4v16M4 9h16M4 15h16"
          stroke="currentColor"
          strokeWidth="1.75"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}
