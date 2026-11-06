import type { ReactNode } from "react";

const S = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

const POINTS: Array<{ icon: ReactNode; title: string; text: string }> = [
  {
    icon: (
      <svg {...S}>
        <path d="M12 3a4 4 0 0 0-4 4v1.2A3.5 3.5 0 0 0 6 14.5 3.5 3.5 0 0 0 9.5 18H12V3z" fill="currentColor" fillOpacity="0.14" />
        <path d="M12 3a4 4 0 0 1 4 4v1.2a3.5 3.5 0 0 1 2 6.3A3.5 3.5 0 0 1 14.5 18H12" />
        <path d="M3.5 3.5l17 17" />
      </svg>
    ),
    title: "Never used for training",
    text: "Your content is not used to train any AI model.",
  },
  {
    icon: (
      <svg {...S}>
        <rect x="3.5" y="4" width="7" height="16" rx="1.5" fill="currentColor" fillOpacity="0.14" />
        <rect x="13.5" y="4" width="7" height="16" rx="1.5" />
        <path d="M6 8h2M6 11.5h2M16 8h2M16 11.5h2" />
      </svg>
    ),
    title: "Kept apart",
    text: "Each company’s information is separate from every other’s.",
  },
  {
    icon: (
      <svg {...S}>
        <circle cx="9" cy="8" r="3.2" fill="currentColor" fillOpacity="0.14" />
        <path d="M3.5 19c.6-3 2.8-4.8 5.5-4.8s4.9 1.8 5.5 4.8" />
        <path d="m15.5 10.5 1.8 1.8 3.4-3.6" />
      </svg>
    ),
    title: "Sharing followed per person",
    text: "Drive, Slack, Linear and GitHub access, for each person.",
  },
  {
    icon: (
      <svg {...S}>
        <path d="M5.5 7h13l-1 12.2a1.8 1.8 0 0 1-1.8 1.6H8.3a1.8 1.8 0 0 1-1.8-1.6z" fill="currentColor" fillOpacity="0.14" />
        <path d="M4 7h16M9.5 7V4.5h5V7M10 11v6M14 11v6" />
      </svg>
    ),
    title: "Deleted when you disconnect",
    text: "Remove a tool and its stored copy is removed too.",
  },
];

/** Section 5's promises: four short points with an icon each, two across. */
export function TrustList() {
  return (
    <ul className="trust">
      {POINTS.map((p) => (
        <li key={p.title}>
          <span className="trust-icon" aria-hidden>{p.icon}</span>
          <b>{p.title}</b>
          <span>{p.text}</span>
        </li>
      ))}
    </ul>
  );
}
