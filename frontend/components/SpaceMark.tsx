import type { CSSProperties } from "react";
import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import type { WorkspaceDetail } from "@/lib/api";

/** Hues a space can take: teal, indigo, amber, rose, sky, green. */
const HUES = [168, 245, 32, 345, 200, 140];

/** One hue per space, from its name, so a room keeps its colour everywhere. */
export function spaceStyle(name: string | null | undefined): CSSProperties {
  let h = 0;
  for (const ch of name || "") h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return { ["--space-h" as string]: HUES[h % HUES.length] };
}

/** The room's letter on its own colour. */
export function SpaceMark({ name, className = "" }: { name: string | null | undefined; className?: string }) {
  return (
    <span className={`space-tile ${className}`} style={spaceStyle(name)} aria-hidden>
      {(name || "S").trim().charAt(0).toUpperCase()}
    </span>
  );
}

/** The sources a space has connected and ready, as logos. */
export function spaceSources(w: WorkspaceDetail | null | undefined): Array<[BrandName, string]> {
  if (!w) return [];
  const out: Array<[BrandName, string]> = [];
  if (w.notion_ready) out.push(["notion", "Notion"]);
  if (w.drive_ready) out.push(["drive", "Google Drive"]);
  if (w.slack_ready) out.push(["slack", "Slack"]);
  if (w.linear_ready) out.push(["linear", "Linear"]);
  if (w.github_connected) out.push(["github", "GitHub"]);
  return out;
}

export function SourceLogos({ sources }: { sources: Array<[BrandName, string]> }) {
  return (
    <span className="space-logos">
      {sources.map(([g, label], i) => (
        <i key={g} title={label} style={{ ["--i" as string]: i }}>
          <BrandGlyph name={g} size={14} />
          <span className="sr-only">{label}</span>
        </i>
      ))}
    </span>
  );
}
