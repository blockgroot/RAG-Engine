const PROVIDER_NAMES: Record<string, string> = {
  linear: "Linear",
  google: "Google Drive",
  drive: "Google Drive",
  notion: "Notion",
  slack: "Slack",
  github: "GitHub",
};

/**
 * Format raw API / OAuth errors into concise, human-friendly messages.
 * Strips technical URLs (like Mozilla developer docs), HTTP status lines,
 * and GraphQL error traces.
 */
export function formatReauthReason(provider: string, rawReason?: string | null): string {
  const provKey = provider.toLowerCase();
  const provName = PROVIDER_NAMES[provKey] ?? (provider.charAt(0).toUpperCase() + provider.slice(1));

  if (!rawReason || !rawReason.trim()) {
    return `${provName} access expired. Reconnect your account to resume syncing.`;
  }

  const raw = rawReason.trim();
  const lower = raw.toLowerCase();

  // Pattern detection for auth expiry / revocation
  const isAuthExpired =
    lower.includes("401") ||
    lower.includes("unauthorized") ||
    lower.includes("invalid_grant") ||
    lower.includes("invalid_token") ||
    lower.includes("token revoked") ||
    lower.includes("access revoked") ||
    lower.includes("expired") ||
    lower.includes("forbidden") ||
    lower.includes("authentication failed") ||
    lower.includes("token has expired");

  if (isAuthExpired) {
    if (provKey === "linear") {
      return "Linear access token expired or was revoked. Reconnect your account to resume syncing.";
    }
    if (provKey === "google" || provKey === "drive") {
      return "Google Drive access expired or was revoked. Reconnect your Google account to resume syncing.";
    }
    if (provKey === "notion") {
      return "Notion access expired or was revoked. Reconnect your Notion workspace to resume syncing.";
    }
    if (provKey === "slack") {
      return "Slack authorization expired or was revoked. Reconnect your Slack workspace to resume syncing.";
    }
    if (provKey === "github") {
      return "GitHub authorization expired or was revoked. Reconnect your GitHub account to resume syncing.";
    }
    return `${provName} access expired or was revoked. Reconnect to resume syncing.`;
  }

  // If it's another error, strip URLs (e.g., https://developer.mozilla.org/...) and clean up
  let cleaned = raw
    .replace(/https?:\/\/\S+/gi, "")
    .replace(/For more information check:?/gi, "")
    .replace(/Client error '401 Unauthorized'/gi, "Unauthorized")
    .replace(/\s+/g, " ")
    .trim();

  // Strip trailing punctuation / colons
  cleaned = cleaned.replace(/[:\-,]+$/, "").trim();

  if (!cleaned || cleaned.length < 5) {
    return `${provName} access expired. Reconnect your account to resume syncing.`;
  }

  // Capitalize first letter and ensure terminal period
  cleaned = cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
  if (!cleaned.endsWith(".")) {
    cleaned += ".";
  }

  return cleaned;
}

