import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

/** Source row with the real app marks — Slack stays labelled soon. */

const SOURCES: Array<{
  id: string;
  mark: BrandName;
  label: string;
  hint: string;
  soon?: boolean;
}> = [
  { id: "notion", mark: "notion", label: "Notion", hint: "Policies & wikis" },
  { id: "drive", mark: "drive", label: "Google Drive", hint: "Docs you already have" },
  { id: "github", mark: "github", label: "GitHub", hint: "Live READMEs & commits" },
  { id: "mail", mark: "gmail", label: "Email", hint: "Magic-link sign-in" },
  { id: "spaces", mark: "workspace", label: "Workspaces", hint: "Meeting notes, not HR" },
  { id: "slack", mark: "slack", label: "Slack", hint: "On the roadmap", soon: true },
];

export function LandingSourcesOrbit() {
  return (
    <ul className="landing-orbit" aria-label="Sources Handbook can use">
      {SOURCES.map((source, i) => (
        <li
          key={source.id}
          className={`landing-orbit-chip landing-orbit-chip-${i + 1}${source.soon ? " is-soon" : ""}`}
        >
          <span className="landing-orbit-mark">
            <BrandGlyph name={source.mark} size={22} />
          </span>
          <span className="landing-orbit-copy">
            <strong>
              {source.label}
              {source.soon ? <em> soon</em> : null}
            </strong>
            <span>{source.hint}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
