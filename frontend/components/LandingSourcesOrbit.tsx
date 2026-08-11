import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

/** Simple integrations strip — logos + labels, no busy card layout. */

const SOURCES: Array<{
  id: string;
  mark: BrandName;
  label: string;
  hint: string;
  size?: number;
  soon?: boolean;
}> = [
  { id: "notion", mark: "notion", label: "Notion", hint: "Policies & wikis" },
  { id: "drive", mark: "drive", label: "Google Drive", hint: "Team documents" },
  { id: "github", mark: "github", label: "GitHub", hint: "READMEs & commits" },
  { id: "mail", mark: "gmail", label: "Email", hint: "Magic-link sign-in", size: 30 },
  { id: "spaces", mark: "workspace", label: "Workspaces", hint: "Team & project spaces" },
  { id: "slack", mark: "slack", label: "Slack", hint: "Coming soon", soon: true },
];

export function LandingSourcesOrbit() {
  return (
    <ul className="landing-integrations" aria-label="Sources Handbook can use">
      {SOURCES.map((source) => (
        <li
          key={source.id}
          className={`landing-integration${source.soon ? " is-soon" : ""}`}
        >
          <span className="landing-integration-mark">
            <BrandGlyph name={source.mark} size={source.size ?? 26} />
          </span>
          <strong>
            {source.label}
            {source.soon ? <em> soon</em> : null}
          </strong>
          <span>{source.hint}</span>
        </li>
      ))}
    </ul>
  );
}
