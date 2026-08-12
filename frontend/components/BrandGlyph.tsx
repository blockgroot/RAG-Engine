/** Official / provided brand marks from /public/brands, plus a couple of
 * inline SVG marks (github, sendgrid) for brands we don't have a static
 * asset for. */

export type BrandName =
  | "notion"
  | "drive"
  | "github"
  | "slack"
  | "sendgrid"
  | "workspace"
  | "secure";

const BRAND_SRC: Record<BrandName, string | null> = {
  sendgrid: null,
  notion: "/brands/notion.png",
  slack: "/brands/slack.png",
  drive: "/brands/drive.png",
  workspace: "/brands/workspace.png",
  secure: "/brands/secure.png",
  github: null,
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

/** Stylized mark for SendGrid — the provider that sends Handbook's magic
 * links/invites. Not a pixel copy of SendGrid's logo (we have no licensed
 * asset for it), just a paper-plane-on-blue glyph in their brand blue that
 * reads as "outbound mail" at a glance. */
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

  return <GithubMark size={size} />;
}
