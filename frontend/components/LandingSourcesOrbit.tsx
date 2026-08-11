import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

/** Creative integrations board — hub + linked sources, not a flat chip grid. */

const SOURCES: Array<{
  id: string;
  mark: BrandName;
  label: string;
  hint: string;
  size?: number;
  soon?: boolean;
  spot: string;
}> = [
  {
    id: "notion",
    mark: "notion",
    label: "Notion",
    hint: "Policies & wikis",
    spot: "a",
  },
  {
    id: "drive",
    mark: "drive",
    label: "Google Drive",
    hint: "Team documents",
    spot: "b",
  },
  {
    id: "github",
    mark: "github",
    label: "GitHub",
    hint: "READMEs & commits",
    spot: "c",
  },
  {
    id: "mail",
    mark: "gmail",
    label: "Email",
    hint: "Magic-link sign-in",
    size: 30,
    spot: "d",
  },
  {
    id: "spaces",
    mark: "workspace",
    label: "Workspaces",
    hint: "Team & project spaces",
    spot: "e",
  },
  {
    id: "slack",
    mark: "slack",
    label: "Slack",
    hint: "Coming soon",
    soon: true,
    spot: "f",
  },
];

export function LandingSourcesOrbit() {
  return (
    <div className="landing-connect" aria-label="Sources Handbook can use">
      <div className="landing-connect-stage">
        <svg className="landing-connect-lines" viewBox="0 0 640 320" aria-hidden>
          <path d="M320 160 L120 72" />
          <path d="M320 160 L520 72" />
          <path d="M320 160 L560 168" />
          <path d="M320 160 L500 260" />
          <path d="M320 160 L140 250" />
          <path d="M320 160 L80 160" />
        </svg>

        <div className="landing-connect-hub">
          <strong>Handbook</strong>
          <span>Your company knowledge</span>
        </div>

        {SOURCES.map((source) => (
          <article
            key={source.id}
            className={`landing-connect-node landing-connect-node-${source.spot}${source.soon ? " is-soon" : ""}`}
          >
            <span className="landing-connect-mark">
              <BrandGlyph name={source.mark} size={source.size ?? 24} />
            </span>
            <span className="landing-connect-copy">
              <strong>
                {source.label}
                {source.soon ? <em> soon</em> : null}
              </strong>
              <span>{source.hint}</span>
            </span>
          </article>
        ))}
      </div>
    </div>
  );
}
