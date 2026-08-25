export type BrandName =
  | "notion"
  | "drive"
  | "github"
  | "slack"
  | "linear"
  | "sendgrid"
  | "workspace"
  | "secure"
  | "private"
  | "document"
  | "schedule";

const BRAND_SRC: Record<BrandName, string | null> = {
  sendgrid: null,
  notion: "/brands/notion.png",
  slack: "/brands/slack.png",
  drive: "/brands/drive.png",
  workspace: "/brands/workspace.png",
  secure: "/brands/secure.png",
  github: null,
  linear: null,
  private: null,
  document: null,
  schedule: null,
};

function GithubMark({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect width="24" height="24" rx="6" fill="#181717" />
      <path
        fill="#fff"
        d="M12 4.4c-4.2 0-7.6 3.4-7.6 7.6 0 3.4 2.2 6.2 5.2 7.2.4.1.5-.2.5-.4v-1.4c-2.1.5-2.6-1-2.6-1-.3-.9-.8-1.1-.8-1.1-.7-.5.1-.5.1-.5.8.1 1.2.8 1.2.8.7 1.2 1.8.8 2.2.6.1-.5.3-.8.5-1-1.7-.2-3.5-.9-3.5-3.8 0-.8.3-1.5.8-2.1-.1-.2-.4-1 .1-2.1 0 0 .7-.2 2.2.8a7.6 7.6 0 0 1 4 0c1.5-1 2.2-.8 2.2-.8.5 1.1.2 1.9.1 2.1.5.6.8 1.3.8 2.1 0 2.9-1.8 3.6-3.5 3.8.3.3.5.7.5 1.5v2.2c0 .2.1.5.5.4 3-.1 5.2-3.8 5.2-7.2 0-4.2-3.4-7.6-7.6-7.6Z"
      />
    </svg>
  );
}

function MailMark({ size }: { size: number }) {
  // The supplied mail icon: a solid blue disc with a white envelope. Drawn
  // rather than dropped in as a PNG so it scales at every size the glyph is
  // used at (18-30px here) and needs no asset step. The `sendgrid` key is
  // historical — every call site means "email", never the vendor.
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <circle cx="12" cy="12" r="12" fill="#2E77BC" />
      <rect x="5.4" y="8" width="13.2" height="8.6" rx="0.9" fill="#fff" />
      <path
        d="M5.4 8.9 12 13.3l6.6-4.4"
        stroke="#2E77BC"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
      <path
        d="M5.9 16.1 10.4 12M18.1 16.1 13.6 12"
        stroke="#2E77BC"
        strokeWidth="1.2"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}

function PrivateMark({ size }: { size: number }) {
  // The supplied privacy icon: a person on a pink disc with a padlock badge.
  // Outlined in ink like the source art, which is why it carries strokes the
  // other marks here do not — it is the one glyph whose meaning ("only these
  // people, locked") depends on reading two objects at a glance.
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <circle cx="12" cy="12" r="11.2" fill="#FB6C93" stroke="#111" strokeWidth="1.1" />
      {/* Shoulders, clipped by the disc. */}
      <path
        d="M3.4 18.6c1.5-3 4.8-4.6 8.6-4.6s7.1 1.6 8.6 4.6A11.2 11.2 0 0 1 3.4 18.6Z"
        fill="#7FD8F2"
        stroke="#111"
        strokeWidth="1.1"
        strokeLinejoin="round"
      />
      {/* Head. */}
      <rect
        x="8.3"
        y="4.4"
        width="7.4"
        height="10"
        rx="3.7"
        fill="#F9BFA6"
        stroke="#111"
        strokeWidth="1.1"
      />
      {/* Padlock badge. */}
      <circle cx="17.6" cy="17.4" r="5" fill="#EAF2FC" stroke="#111" strokeWidth="1.1" />
      <path
        d="M15.9 16.6v-1.1a1.7 1.7 0 0 1 3.4 0v1.1"
        stroke="#111"
        strokeWidth="1.1"
        fill="none"
        strokeLinecap="round"
      />
      <rect
        x="15.2"
        y="16.5"
        width="4.8"
        height="3.6"
        rx="0.5"
        fill="#FBD024"
        stroke="#111"
        strokeWidth="1.1"
      />
    </svg>
  );
}

function LinearMark({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect width="24" height="24" rx="6" fill="#5E6AD2" />
      <circle cx="14.5" cy="9.5" r="6" fill="#fff" />
      <g stroke="#fff" strokeWidth="2.2" strokeLinecap="round">
        <line x1="10.8" y1="16.2" x2="7.2" y2="19.8" />
        <line x1="8.3" y1="13.4" x2="4.6" y2="17.1" />
        <line x1="6.4" y1="10.2" x2="3.2" y2="13.4" />
      </g>
    </svg>
  );
}

function DocumentMark({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect width="24" height="24" rx="6" fill="#0e7490" />
      <path fill="#fff" d="M8 4h6.5L18 7.5V19a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z" />
      <path fill="#0e7490" fillOpacity="0.35" d="M14.5 4v3.2c0 .17.13.3.3.3H18L14.5 4Z" />
      <rect x="9.3" y="11" width="6" height="1.3" rx="0.65" fill="#0e7490" />
      <rect x="9.3" y="14" width="6" height="1.3" rx="0.65" fill="#0e7490" />
      <rect x="9.3" y="17" width="4.2" height="1.3" rx="0.65" fill="#0e7490" />
    </svg>
  );
}

function ScheduleMark({ size }: { size: number }) {
  // A calendar with a clock over it — the same idea as the icon supplied for
  // the scheduler, redrawn as a 24x24 rounded-square glyph so it sits in the
  // same family as the other marks here (a full-colour illustration would be
  // the only glyph on the page with its own outline weight).
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect width="24" height="24" rx="6" fill="#0f766e" />
      <rect x="4.5" y="6" width="15" height="13.5" rx="2" fill="#fff" />
      <rect x="4.5" y="6" width="15" height="3.6" rx="2" fill="#ef4444" />
      {/* Binder rings, as on the source icon. */}
      <rect x="7.6" y="3.8" width="1.5" height="3.4" rx="0.75" fill="#e2e8e6" />
      <rect x="14.9" y="3.8" width="1.5" height="3.4" rx="0.75" fill="#e2e8e6" />
      {/* Ticked days. */}
      <rect x="6.6" y="11.4" width="2.6" height="2.6" rx="0.7" fill="#5eead4" />
      <rect x="10.2" y="11.4" width="2.6" height="2.6" rx="0.7" fill="#5eead4" />
      <rect x="6.6" y="15" width="2.6" height="2.6" rx="0.7" fill="#cbd5d3" />
      {/* Clock, bottom-right, as on the source icon. */}
      <circle cx="16" cy="16" r="4.3" fill="#facc15" stroke="#0f766e" strokeWidth="1.1" />
      <path
        d="M16 13.9V16h1.7"
        stroke="#0f766e"
        strokeWidth="1.1"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}

export function BrandGlyph({
  name,
  size = 28,
}: {
  name: BrandName;
  size?: number;
}) {
  const src = BRAND_SRC[name];
  if (src) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- static brand assets from /public
      <img
        src={src}
        alt=""
        width={size}
        height={size}
        aria-hidden
        className="brand-glyph-img"
        style={{ width: size, height: size }}
      />
    );
  }

  if (name === "sendgrid") {
    return <MailMark size={size} />;
  }

  if (name === "private") {
    return <PrivateMark size={size} />;
  }

  if (name === "linear") {
    return <LinearMark size={size} />;
  }

  if (name === "document") {
    return <DocumentMark size={size} />;
  }

  if (name === "schedule") {
    return <ScheduleMark size={size} />;
  }

  return <GithubMark size={size} />;
}
