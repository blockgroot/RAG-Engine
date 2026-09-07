export type FeatureEmojiName =
  | "document"
  | "chart"
  | "schedule"
  | "sentiment"
  | "model"
  | "workspace"
  | "secure"
  | "private";

const EMOJI: Record<FeatureEmojiName, string> = {
  document: "📄",
  chart: "📊",
  schedule: "⏱️",
  sentiment: "💬",
  model: "✨",
  workspace: "🧩",
  secure: "🛡️",
  private: "🔒",
};

export function FeatureEmoji({ name }: { name: FeatureEmojiName }) {
  return <span className={`feature-emoji feature-emoji-${name}`} aria-hidden>{EMOJI[name]}</span>;
}
