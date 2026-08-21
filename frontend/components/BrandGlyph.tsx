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
  | "document";

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

function SendgridMark({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect width="24" height="24" rx="6" fill="#1A82E2" />
      <path
        fill="#fff"
        d="M5 12.4 18.4 6.2c.5-.2 1 .3.8.8l-3.4 12.9c-.1.5-.7.7-1.1.4l-3.4-2.6-2 1.9c-.3.3-.8.1-.8-.3v-3l7.4-6.8c.2-.2 0-.5-.2-.4L7 13.4l-2-1c-.4-.2-.4-.8 0-1Z"
      />
    </svg>
  );
}

function PrivateMark({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect width="24" height="24" rx="6" fill="#0d5c56" />
      <circle cx="10.3" cy="8.6" r="3.1" fill="#fff" />
      <path
        fill="#fff"
        d="M5 18.1c0-3.3 2.4-5.6 5.3-5.6 1.1 0 2.1.3 2.9.9a4.5 4.5 0 0 0-1.6 3.5c0 .4.06.9.18 1.3H5.3c-.2-.5-.3-1-.3-1.1Z"
      />
      <path
        fill="#fff"
        d="M15.8 12.5a2.5 2.5 0 0 1 2.5 2.5v.6h.2c.6 0 1.1.5 1.1 1.1v2.5c0 .6-.5 1.1-1.1 1.1h-5.4c-.6 0-1.1-.5-1.1-1.1v-2.5c0-.6.5-1.1 1.1-1.1h.2v-.6a2.5 2.5 0 0 1 2.5-2.5Zm0 1.3c-.7 0-1.2.5-1.2 1.2v.6h2.4v-.6c0-.7-.5-1.2-1.2-1.2Z"
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
    return <SendgridMark size={size} />;
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

  return <GithubMark size={size} />;
}
