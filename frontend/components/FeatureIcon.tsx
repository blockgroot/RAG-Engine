export type FeatureIconName =
  | "document"
  | "chart"
  | "schedule"
  | "sentiment"
  | "model"
  | "workspace"
  | "secure"
  | "private"
  | "code"
  | "refresh"
  | "link"
  | "invite";

const ICONS: Record<FeatureIconName, string> = {
  document: "description",
  chart: "bar_chart",
  schedule: "schedule",
  sentiment: "forum",
  model: "auto_awesome",
  workspace: "widgets",
  secure: "shield",
  private: "lock",
  code: "code",
  refresh: "autorenew",
  link: "cable",
  invite: "group_add",
};

export function FeatureIcon({ name }: { name: FeatureIconName }) {
  return (
    <span className={`feature-icon feature-icon-${name} material-symbols-rounded`} aria-hidden>
      {ICONS[name]}
    </span>
  );
}
