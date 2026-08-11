/** Official / provided brand marks from /public/brands. */

export type BrandName =
  | "notion"
  | "drive"
  | "github"
  | "slack"
  | "gmail"
  | "workspace"
  | "secure";

const BRAND_SRC: Record<BrandName, string | null> = {
  gmail: "/brands/gmail.png",
  notion: "/brands/notion.png",
  slack: "/brands/slack.png",
  drive: "/brands/drive.png",
  workspace: "/brands/workspace.png",
  secure: "/brands/secure.png",
  github: null,
};

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

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden
    >
      <rect width="24" height="24" rx="6" fill="#181717" />
      <path
        fill="#fff"
        d="M12 4.4c-4.2 0-7.6 3.4-7.6 7.6 0 3.4 2.2 6.2 5.2 7.2.4.1.5-.2.5-.4v-1.4c-2.1.5-2.6-1-2.6-1-.3-.9-.8-1.1-.8-1.1-.7-.5.1-.5.1-.5.8.1 1.2.8 1.2.8.7 1.2 1.8.8 2.2.6.1-.5.3-.8.5-1-1.7-.2-3.5-.9-3.5-3.8 0-.8.3-1.5.8-2.1-.1-.2-.4-1 .1-2.1 0 0 .7-.2 2.2.8a7.6 7.6 0 0 1 4 0c1.5-1 2.2-.8 2.2-.8.5 1.1.2 1.9.1 2.1.5.6.8 1.3.8 2.1 0 2.9-1.8 3.6-3.5 3.8.3.3.5.7.5 1.5v2.2c0 .2.1.5.5.4 3-.1 5.2-3.8 5.2-7.2 0-4.2-3.4-7.6-7.6-7.6Z"
      />
    </svg>
  );
}
