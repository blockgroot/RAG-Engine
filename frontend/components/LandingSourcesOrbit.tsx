import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

/** Simple integrations strip — logos + labels, no busy card layout.
 *
 * Split into two rows rather than one flat grid: CONNECTORS is where the
 * list actually grows over time (a new source is one more entry here), so it
 * gets its own row that can wrap on its own terms. PLATFORM is fixed —
 * sign-in and workspaces aren't content sources and won't multiply the same
 * way — keeping them separate stops a growing connector count from ever
 * pushing a lone orphan item onto a ragged final row.
 */

type Integration = {
  id: string;
  mark: BrandName;
  label: string;
  hint: string;
  size?: number;
  soon?: boolean;
};

const CONNECTORS: Integration[] = [
  { id: "notion", mark: "notion", label: "Notion", hint: "Docs & wikis" },
  { id: "drive", mark: "drive", label: "Google Drive", hint: "Team documents" },
  { id: "github", mark: "github", label: "GitHub", hint: "READMEs & commits" },
  { id: "slack", mark: "slack", label: "Slack", hint: "Channel conversations" },
  { id: "linear", mark: "linear", label: "Linear", hint: "Issues & tickets" },
];

const PLATFORM: Integration[] = [
  {
    id: "reports",
    mark: "schedule",
    label: "Scheduled reports",
    hint: "Weekly or monthly, emailed",
  },
  { id: "mail", mark: "sendgrid", label: "Email", hint: "Magic-link sign-in", size: 30 },
  { id: "spaces", mark: "workspace", label: "Workspaces", hint: "Team & project spaces" },
];

function IntegrationRow({ label, items }: { label: string; items: Integration[] }) {
  return (
    <div className="landing-integration-group">
      <p className="landing-integration-group-label">{label}</p>
      <ul className="landing-integrations" aria-label={label}>
        {items.map((source) => (
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
    </div>
  );
}

export function LandingSourcesOrbit() {
  return (
    <div className="landing-integration-groups">
      <IntegrationRow label="Connectors" items={CONNECTORS} />
      <IntegrationRow label="Also included" items={PLATFORM} />
    </div>
  );
}
