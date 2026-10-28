/** Typed fetch wrapper for the FastAPI backend. */

function resolveApiBaseUrl(): string {
  const raw = (process.env.NEXT_PUBLIC_API_BASE_URL || "/api").trim();
  return raw.replace(/\/$/, "");
}

const API_BASE_URL = resolveApiBaseUrl();

export class ApiError extends Error {
  status: number;
  code: string | null;
  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function parseApiDetail(detail: unknown): { message: string; code: string | null } {
  if (typeof detail === "string") return { message: detail, code: null };
  if (detail && typeof detail === "object") {
    const d = detail as { message?: unknown; code?: unknown; detail?: unknown };
    if (typeof d.message === "string") {
      return { message: d.message, code: typeof d.code === "string" ? d.code : null };
    }
  }
  return { message: "Request failed", code: null };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const parsed = parseApiDetail(body.detail ?? body);
    throw new ApiError(response.status, parsed.message, parsed.code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

/** The three downvote categories. A closed set because the point of a category
 *  is that it groups: "out of date" sends an admin to re-sync a source,
 *  "incorrect" sends them to rewrite a document. Anything else goes in the
 *  comment box. */
export const FEEDBACK_REASONS = [
  { value: "incorrect", label: "Incorrect information" },
  { value: "out_of_date", label: "Out of date" },
  { value: "unhelpful", label: "Too long / unhelpful" },
] as const;

export type FeedbackReason = (typeof FEEDBACK_REASONS)[number]["value"];

/** Questions people couldn't get answered, grouped by meaning. Counts only,
 *  never who asked. */
export interface GapTopic {
  id: string;
  /** The first question asked on this topic, in the asker's own words. */
  label: string;
  /** Enough different people hit it for the bell to say so. */
  alerting: boolean;
  missing_people: number;
  wrong_people: number;
  questions: string[];
  /** Null: nothing came close (the document is missing). A number: something
   *  related exists but was too weak a match. */
  best_score: number | null;
  wrong_answer: string | null;
  wrong_reason: string | null;
  wrong_comment: string | null;
  surfaces: string[];
  last_asked: string;
}

export interface ConversationSummary {
  id: string;
  /** The first question asked. No generated title: one more thing to keep
   *  true, and people recognise a chat by what they asked. */
  title: string | null;
  turn_count: number;
  attachment_count: number;
  created_at: string;
  last_activity_at: string;
}

export interface ConversationTurn {
  turn_index: number;
  question: string;
  answer: string;
  created_at: string;
  /** Sources drawn under this answer. Empty on a turn saved before they were kept. */
  cited?: { n: number; document_id: string; title: string | null; provider: string | null; url: string | null }[];
  /** The chart drawn with this answer, as it was drawn. Absent when there was none. */
  chart?: { panel: InsightPanel; period: string | null } | null;
  /** Who answered, for the provenance pill. Absent on a turn saved before it was kept. */
  meta?: TurnMeta | null;
}

export interface TurnMeta {
  source: string;
  agent?: string;
  connected_providers?: string[];
  attachments?: string[];
  live_sources?: { provider: string; fetched_at: string }[];
  model?: string | null;
  citation_count: number;
}

export interface RejectedFile {
  filename: string;
  /** Why this one file was refused — written for the uploader, so it is shown
   *  as-is rather than replaced with a generic message. */
  reason: string;
}

/** Partial success is the normal outcome: some files land, some are named
 *  with a reason. */
export interface UploadResult {
  attachments: Attachment[];
  rejected: RejectedFile[];
}

export interface Attachment {
  id: string;
  filename: string;
  char_count: number;
  /** True when the file was longer than the server's page/row/char budget.
   *  Shown in the chip: a partial file that looks complete is the failure the
   *  whole feature is arranged against. */
  truncated: boolean;
  /** Parts of the file read like instructions to an AI. Still used as a
   *  source, but the model is told to treat all of it as data. */
  flagged?: boolean;
}

/** The address you sign in with, and the ones you used before. Documents shared
 *  with a prior address stay readable to you. */
export interface SignInEmails {
  email: string | null;
  prior: string[];
}

/** One of the member's own accounts in a connected tool, linked to them. */
export interface LinkedIdentity {
  id: string;
  provider: string;
  account: string;
  email: string | null;
  display_name: string | null;
  verified_by: "provider_email" | "oauth" | null;
  can_unlink: boolean;
}

export interface LinkedIdentities {
  identities: LinkedIdentity[];
  github_link_available: boolean;
}

export interface MemoryFact {
  id: string;
  kind: "preference" | "context" | "interest";
  text: string;
  pinned: boolean;
  created_at: string | null;
}

export interface PersonalMemory {
  /** Off for the whole deployment: nothing is remembered anywhere. */
  available: boolean;
  org_enabled: boolean;
  enabled: boolean;
  can_manage_org: boolean;
  facts: MemoryFact[];
}

export interface Me {
  user_id: string;
  org_id: string;
  org_name: string | null;
  email: string | null;
  role: "admin" | "member";
  has_connection: boolean;
  has_documents: boolean;
  sync_in_progress: boolean;
  latest_job_status: string | null;
  latest_doc_count: number | null;
  ready_to_ask: boolean;
  policy_ready: boolean;
  github_connected: boolean;
  /** Act (writes into connected tools) is switched on for Handbook. */
  act_enabled?: boolean;
  slack_ready: boolean;
  linear_ready: boolean;
  notion_ready: boolean;
  drive_ready: boolean;
}

/** What a chain drafts next, once the cards it waited on are decided:
 *  "open a GitHub issue, then comment its link on Linear" drafts the comment
 *  after the issue exists. Each new card is approved on its own. */
export interface ActNext {
  reply: string;
  actions: ActCard[];
}

/** One proposed (or finished) write, as the Act page shows it. The payload
 *  is exactly what will be sent; approving sends back its hash. */
export interface ActCard {
  id: string;
  action: string;
  label: string;
  provider: string | null;
  /** Whose name the tool will show: "as you", or "via Handbook Actions, for you"
   *  where the tool records its own bot (Notion pages). */
  attribution: string | null;
  target: { kind: string; external_id: string; name: string; url: string | null };
  payload: { title?: string; body?: string; text?: string; details?: [string, string][] };
  /** The drafted words as shown and edited (a Slack post without the line
   *  naming who asked). Only these can be edited, never where it goes. */
  draft: { title?: string; body?: string };
  /** The person changed the model's draft before approving. */
  edited: boolean;
  payload_hash: string;
  status: "proposed" | "executing" | "succeeded" | "failed" | "unknown" | "rejected" | "expired";
  /** none | narrower: nobody new sees private content. wider | unknown: the
   *  person must tick the acknowledgement before Approve works. */
  exposure: "none" | "narrower" | "wider" | "unknown";
  acknowledged: boolean;
  /** Titles of the private documents the draft drew on. */
  sources: string[];
  audience_size: number | null;
  outsiders: number | null;
  result: { external_id?: string; url?: string | null; error?: string } | null;
  expires_at: string;
  created_at: string;
  user_email?: string | null;
}

/** What this person can draft now: the MCP tools turned on for servers they signed in to. */
export interface ActStatus {
  enabled: boolean;
  actions: { name: string; label: string; provider: string }[];
  recent?: ActCard[];
}

export interface SpaceAct {
  company: "off" | "on";
  enabled: boolean;
  can_change: boolean;
}

/** A tool the admin switched on, as a space's owner sees it: on unless they switched it off here. */
export interface SpaceTool {
  name: string;
  title: string;
  description: string;
  read_only: boolean;
  destructive: boolean;
  on: boolean;
}

export interface SpaceToolServer {
  id: string;
  name: string;
  group: string | null;
  group_name: string | null;
  brand: string | null;
  on: boolean;
  tools: SpaceTool[];
}

export interface AdminActions {
  enabled: boolean;
  block_wider: boolean;
  /** Slack DMs / Slack channels, each switched on apart. */
  surfaces: { name: string; label: string; enabled: boolean }[];
}

/** A tool of an MCP server an admin added. "lookup" runs while drafting, without a card. */
export interface McpTool {
  name: string;
  title: string;
  description: string;
  read_only: boolean;
  /** Deletes or can't be undone: never turned on by "Turn all on". */
  destructive: boolean;
  mode: "off" | "lookup" | "write";
}

export interface McpServerAdmin {
  id: string;
  name: string;
  url: string;
  auth: "none" | "oauth";
  enabled: boolean;
  /** Servers of one product (Google Workspace) share a row and a sign-in. */
  group: string | null;
  tools: McpTool[];
}

/** A connector's own MCP server, added in one click. */
export interface McpPreset {
  key: string;
  name: string;
  url: string | null;
  note: string;
  added: boolean;
  group?: string;
}

export interface McpServerMine {
  id: string;
  name: string;
  auth: "none" | "oauth";
  tools_on: number;
  connected: boolean;
  needs_reauth: boolean;
}

export interface MemberRecord {
  id: string;
  email: string;
  role: "admin" | "member";
  created_at: string;
}

export interface SyncChanges {
  connection_id: string;
  new_count: number;
  updated_count: number;
  removed_count: number;
  unchanged_count: number;
  remote_total: number;
  has_changes: boolean;
  /**
   * Channels renamed in Slack since the labels were stored. Reported apart
   * from `has_changes` because a rename needs no sync — no message id or
   * timestamp moved — but the client should drop cached labels and can say
   * what moved.
   */
  renamed?: { from: string; to: string }[];
}

export interface GitHubRepoRef {
  full_name: string;
  description: string | null;
  topics: string[];
}

export interface ConnectionSourceConfig {
  folder_id?: string;
  folder_name?: string;
  installation_id?: string;
  /** GitHub: the installation's own settings page (organization or personal). */
  manage_url?: string;
  account_login?: string;
  repository_selection?: "all" | "selected";
  repos?: GitHubRepoRef[];
  channel_ids?: string[];
  channel_names?: Record<string, string>;
  /** Linear: the teams this connection reads. */
  team_ids?: string[];
  team_names?: Record<string, string>;
  /** Google Forms allow-list for survey sentiment. Empty = read nothing. */
  form_ids?: string[];
  form_titles?: Record<string, string>;
}

export interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
  is_member: boolean;
}

export interface LinearTeam {
  id: string;
  key: string;
  name: string;
  private: boolean;
}

export interface SlackChannelMember {
  id: string;
  name: string;
  email: string;
  already_org_member: boolean;
  already_workspace_member: boolean;
}

export interface GitHubScopeResponse {
  connection_id: string;
  provider: "github";
  account_login: string;
  repository_selection: "all" | "selected";
  repo_count: number;
  repos: GitHubRepoRef[];
}

export interface ConnectionRecord {
  id: string;
  provider: "notion" | "google" | "github" | "slack" | "linear";
  external_workspace_name: string | null;
  created_at: string;
  source_config?: ConnectionSourceConfig | null;
  needs_reauth?: boolean;
  reauth_reason?: string | null;
  /** When the background sync last looked at this connection. */
  last_sync_at?: string | null;
}

export interface GoogleForm {
  id: string;
  title: string;
}

export interface FormsSelection {
  /** GOOGLE_FORMS_ENABLED on the server. False = nothing to pick yet. */
  enabled: boolean;
  forms: GoogleForm[];
  selected: string[];
}

/** Files in the chosen folder whose sharing Google will not report, so they
 *  cannot be indexed. Returned by the folder-save routes and shown to whoever
 *  picked the folder, right there — `null` when there is nothing to say. */
export interface SharingWarning {
  kind: "permissions";
  count: number;
  checked: number;
  files: string[];
  truncated: boolean;
  title: string;
  detail: string;
  fix: string;
}

export interface ConnectionConfigResponse {
  connection_id: string;
  provider: string;
  config: ConnectionSourceConfig;
  folder_changed?: boolean;
  channels_changed?: boolean;
  teams_changed?: boolean;
  documents_purged?: number;
  sharing?: SharingWarning | null;
}

export interface DriveFolder {
  id: string;
  name: string;
}

export interface JobRecord {
  id: string;
  connection_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  doc_count: number | null;
  error: string | null;
  phase: string | null;
  total_documents: number | null;
  processed_documents: number;
  /** Files the last sync could not read the sharing of, so they were left out
   *  (new) or kept the viewers they already had (already indexed). */
  permission_unreadable_documents?: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface MagicLinkResponse {
  status: "sent" | "no_account";
  message: string;
  /** False on a self-hosted copy: its one company exists, others join by invite. */
  signup_enabled?: boolean;
  dev_link: string | null;
}

export interface SignupResponse {
  message: string;
}

export interface WorkspaceRecord {
  id: string;
  name: string;
  role: "owner" | "member";
  created_by: string | null;
}

/** Workspace detail + the same readiness shape as ``Me`` / ``GET /me``. */
export interface WorkspaceDetail extends WorkspaceRecord {
  has_connection: boolean;
  has_documents: boolean;
  sync_in_progress: boolean;
  latest_job_status: string | null;
  latest_doc_count: number | null;
  ready_to_ask: boolean;
  /** Whether THIS workspace's legacy "Docs" tab has content of its own (see `Me.policy_ready`). */
  policy_ready: boolean;
  /**
   * True when THIS workspace has its own GitHub connection. Scoped to the
   * workspace on purpose: an org-wide GitHub connection must not light up a
   * workspace's Code tab, or a member would be offered code they aren't scoped
   * to read.
   */
  github_connected: boolean;
  /** Whether THIS workspace has its own ingested Slack threads (same rule as `Me.slack_ready`). */
  slack_ready: boolean;
  /** Whether THIS workspace has its own ingested Linear issues (same rule as `Me.linear_ready`). */
  linear_ready: boolean;
  /** Whether THIS workspace has its own ingested Notion pages (same rule as `Me.notion_ready`). */
  notion_ready: boolean;
  /** Whether THIS workspace has its own ingested Drive documents (same rule as `Me.drive_ready`). */
  drive_ready: boolean;
}

export interface WorkspaceMemberRecord {
  user_id: string;
  email: string;
  role: "owner" | "member";
  joined_at: string;
}

/** A recurring activity report the signed-in person owns. */
export interface SchedulerRecord {
  id: string;
  provider: string;
  frequency: "daily" | "weekly" | "monthly";
  /** The user's own standing instruction, re-applied on every run. */
  prompt: string;
  /** "active" while scheduled; "failed" once it gave up after repeated errors. */
  status: string;
  last_run_at: string | null;
  next_run_at: string;
  /** Why the most recent run failed, if it did. Cleared on the next success. */
  last_error: string | null;
  created_at: string;
  /** null = the company-wide connection; set = one space's own connection. */
  workspace_id: string | null;
  /** Resolved name for `workspace_id`, so a card can show it without a lookup. */
  workspace_name: string | null;
}

/** One generated report, as a row in "Your reports". */
export interface ReportRow {
  id: string;
  scheduler_id: string | null;
  provider: string;
  frequency: "daily" | "weekly" | "monthly";
  /** The standing request the report answers — used as its title. */
  title: string;
  /** The space it read, or null for company-wide. */
  space_name: string | null;
  item_count: number;
  /** Whether the notification email was accepted. The report is readable regardless. */
  delivered: boolean;
  window_start: string;
  window_end: string;
  created_at: string;
}

/** A report plus its body. Only the detail route returns these three fields. */
export interface ReportDetail extends ReportRow {
  report_text: string;
  /**
   * Activity the report was built from; links are rendered from here.
   * `meta` is the who/where/when prefix, kept apart from `summary` so a row
   * can set it small rather than letting it compete with the content. Null on
   * reports generated before items carried it.
   */
  items: { summary: string; url: string | null; meta?: string | null }[];
  notes: string[];
}

/**
 * A connected service a report can be built against. Only providers with a
 * real "activity since" feed appear here (GitHub, Slack) — a connected
 * Notion/Drive is deliberately absent, since a report on it would fail every
 * cycle.
 */
export interface SchedulableConnection {
  id: string;
  provider: string;
  workspace_name: string | null;
  /** "org" = the company-wide connection; "workspace" = one space's own. */
  scope: "org" | "workspace";
  /** The space this connection belongs to, or null for the company-wide one. */
  space_id: string | null;
  space_name: string | null;
  /**
   * Names of what this connection can actually read — the picked Slack
   * channels, the authorized GitHub repos. Empty for Linear, whose scope is
   * "whatever the token can see" with no stored subset to name.
   */
  topics: string[];
}

/**
 * A scope a report can be built in: the company, or one space the signed-in
 * person belongs to.
 *
 * `providers` is what can actually be scheduled here. `connected` is
 * everything connected to the space including sources with no activity feed
 * yet (Notion, Drive) — a space appears even when `providers` is empty, so
 * "Meeting notes has nothing schedulable yet" is visible rather than looking
 * like a missing space.
 */
export interface SchedulerSpace {
  id: string | null;
  name: string;
  scope: "org" | "workspace";
  providers: string[];
  connected: string[];
}

/**
 * One turn of the conversational setup flow. Either the assistant needs more
 * from the user (`done: false`, show `reply`) or it had everything and the
 * report now exists (`done: true`).
 */
export interface SetupChatResponse {
  done: boolean;
  reply?: string;
  scheduler?: SchedulerRecord;
}

export interface SetupChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface OrgModel {
  model: string;
  label: string;
  preset: string | null;
  preset_label: string | null;
  key_tail: string | null;
  checked_at: string | null;
  saved_at: string | null;
  /** Agent mode's tool-calling test at save; null for a model saved before it. */
  agent: "native" | "text" | "none" | "unknown" | null;
}

export interface ModelPreset {
  id: string;
  label: string;
  models_url: string;
}

export interface ModelChoice {
  id: string;
  label: string;
  note: string;
  /** Builds charts. `null` for the company's own model: not ours to judge,
   *  so Chart mode offers it with a note. */
  charts?: boolean | null;
}

// ---------------------------------------------------------------------------
// Insights (the Visualizations section)
// ---------------------------------------------------------------------------

export type InsightScope = {
  /** null is the company, mirroring `workspace_id IS NULL` server-side. */
  id: string | null;
  name: string;
  providers: string[];
  /** Which of those we can actually chart yet. Reported separately so
   *  "Slack is connected but has no charts" is visible instead of looking
   *  like an empty dashboard. */
  chartable: string[];
};

export type InsightPanel = {
  id: string;
  provider: string;
  title: string;
  chart: string;
  group_by: string | null;
  /** A second grouping from the query grammar; drawn as "group · split". */
  split_by?: string | null;
  unit: string;
  caveat: string;
  /** null means the panel failed; [] means it ran and there is nothing to
   *  show. Those need different copy, so they are different values. */
  points:
    | {
        bucket: string;
        group: string | null;
        /** A second dimension, only for charts that need two (a
         *  diverging bar is topic BY sentiment label). */
        series?: string | null;
        value: number;
      }[]
    | null;
  /** Facts only exist from the first sync after this shipped, so a chart
   *  whose axis starts on deploy day would read as if nobody worked before. */
  measured_since: string | null;
  /** One sentence on what each bar/point is ("Each bar adds up Salary for
   *  the rows of each Team. 10 rows, ₹266 lakh in total."). */
  explain?: string;
  /** What an empty value means per field in this tool ("Unassigned"). */
  blank_labels?: Record<string, string>;
  /** Unit per series, for a chart of two measures. */
  series_units?: Record<string, string>;
  /** Measures the question asked for that were not in the data. */
  missing?: string[];
  /** Set when the chart is a table from a document or upload: drawn in the
   *  document's order, never ranked. */
  table?: { id: string; name: string; document: string; origin: string; upload: boolean } | null;
  /** The rows the bars are made of: what, who, when, and where to open it.
   *  A number nobody can trace is a number nobody trusts. */
  details?: {
    subject?: string | null;
    actor?: string | null;
    state?: string | null;
    at?: string | null;
    url?: string | null;
    attrs?: Record<string, unknown>;
  }[];
};

export type InsightDashboard = {
  scope: string | null;
  scope_name: string;
  period: string;
  window_days: number;
  panels: InsightPanel[];
};

export type InsightAskResult =
  /** "I can't chart that, here is what I can" is an ANSWER, not an error -
   *  which is why the failure case is a 200 with a message. */
  | { charted: false; message: string }
  | {
      charted: true;
      spec: { metric: string; group_by: string | null; period: string };
      panel: InsightPanel;
    };

export type InsightPin = {
  id: string;
  scope: string | null;
  metric: string;
  group_by: string | null;
  period: string;
  title: string;
};

export type ConnectorFreshness = {
  provider: string;
  last_sync_at: string | null;
  needs_reauth: boolean;
  chartable: boolean;
};

export type Notification = {
  kind: "reauth" | "scope" | "injection" | "gap";
  severity: "high" | "medium";
  provider: string;
  scope: string;
  title: string;
  detail: string;
  action: string;
  href: string;
};

export const api = {
  signup: (email: string, companyName: string) =>
    request<SignupResponse>("/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, company_name: companyName }),
    }),

  requestMagicLink: (email: string) =>
    request<MagicLinkResponse>("/auth/magic-link", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  me: () => request<Me>("/me"),
  notifications: () =>
    request<{ items: Notification[]; count: number }>("/notifications"),
  logout: () =>
    request<{ status: string }>("/auth/logout", { method: "POST" }),

  connectUrl: (provider: string) => `${API_BASE_URL}/auth/${provider}/authorize`,

  githubInstallPending: (token: string) =>
    request<{
      scope: "org" | "workspace";
      workspace_id: string | null;
      installations: Array<{
        id: string;
        login: string;
        account_type: string;
        available: boolean;
        unavailable_reason: string | null;
      }>;
      hint: string;
      install_another_url: string;
      switch_account_url: string;
    }>(`/auth/github/installations/pending/${encodeURIComponent(token)}`),

  chooseGitHubInstall: (token: string, installationId: string) =>
    request<{ redirect_to: string }>(
      `/auth/github/installations/pending/${encodeURIComponent(token)}`,
      {
        method: "POST",
        body: JSON.stringify({ installation_id: installationId }),
      }
    ),


  listMembers: () => request<MemberRecord[]>("/admin/members"),
  inviteMember: (email: string) =>
    request<{ id: string; email: string; role: string; dev_link?: string | null }>(
      "/admin/members",
      {
        method: "POST",
        body: JSON.stringify({ email }),
      }
    ),
  revokeMemberSessions: (userId: string) =>
    request<{ status: string; user_id: string }>(`/admin/members/${userId}/revoke-sessions`, {
      method: "POST",
    }),
  promoteMember: (userId: string) =>
    request<{ id: string; email: string; role: string }>(`/admin/members/${userId}/promote`, {
      method: "POST",
    }),
  demoteMember: (userId: string) =>
    request<{ id: string; email: string; role: string }>(`/admin/members/${userId}/demote`, {
      method: "POST",
    }),
  removeMember: (userId: string) =>
    request<{ status: string; user_id: string }>(`/admin/members/${userId}`, {
      method: "DELETE",
    }),

  listConnections: () => request<ConnectionRecord[]>("/admin/connections"),
  getConnectionConfig: (connectionId: string) =>
    request<ConnectionConfigResponse>(`/admin/connections/${connectionId}/config`),
  searchConnectionDriveFolders: (connectionId: string, q: string) =>
    request<{ folders: DriveFolder[] }>(
      `/admin/connections/${connectionId}/drive-folders?q=${encodeURIComponent(q)}`
    ),
  setConnectionConfig: (connectionId: string, folderUrl: string) =>
    request<ConnectionConfigResponse>(`/admin/connections/${connectionId}/config`, {
      method: "PUT",
      body: JSON.stringify({ folder_url: folderUrl }),
    }),
  listConnectionSlackChannels: (connectionId: string) =>
    request<{ channels: SlackChannel[] }>(
      `/admin/connections/${connectionId}/slack-channels`
    ),
  setConnectionSlackChannels: (connectionId: string, channelIds: string[]) =>
    request<ConnectionConfigResponse>(`/admin/connections/${connectionId}/config`, {
      method: "PUT",
      body: JSON.stringify({ channel_ids: channelIds }),
    }),
  /** Linear teams for the picker; `workspaceId` reads that space's connection. */
  listLinearTeams: (connectionId: string, workspaceId?: string) =>
    request<{ teams: LinearTeam[] }>(
      workspaceId
        ? `/workspaces/${workspaceId}/connections/${connectionId}/linear-teams`
        : `/admin/connections/${connectionId}/linear-teams`
    ),
  setLinearTeams: (connectionId: string, teamIds: string[], workspaceId?: string) =>
    request<ConnectionConfigResponse>(
      workspaceId
        ? `/workspaces/${workspaceId}/connections/${connectionId}/config`
        : `/admin/connections/${connectionId}/config`,
      { method: "PUT", body: JSON.stringify({ team_ids: teamIds }) }
    ),
  // Google Forms survey sentiment. `enabled` is the deployment flag
  // (GOOGLE_FORMS_ENABLED), reported rather than 404'd so the picker can say
  // "your deployment has not switched this on" instead of looking broken.
  listConnectionForms: (connectionId: string) =>
    request<FormsSelection>(`/admin/connections/${connectionId}/forms`),
  setConnectionForms: (connectionId: string, formIds: string[]) =>
    request<{ selected: string[] }>(`/admin/connections/${connectionId}/forms`, {
      method: "PUT",
      body: JSON.stringify({ form_ids: formIds }),
    }),
  listWorkspaceConnectionForms: (workspaceId: string, connectionId: string) =>
    request<FormsSelection>(
      `/workspaces/${workspaceId}/connections/${connectionId}/forms`
    ),
  setWorkspaceConnectionForms: (
    workspaceId: string,
    connectionId: string,
    formIds: string[]
  ) =>
    request<{ selected: string[] }>(
      `/workspaces/${workspaceId}/connections/${connectionId}/forms`,
      { method: "PUT", body: JSON.stringify({ form_ids: formIds }) }
    ),
  actStatus: () => request<ActStatus>("/act/status"),
  confirmAction: (id: string, payloadHash: string, acknowledged: boolean) =>
    request<{ action: ActCard; next?: ActNext | null }>(`/act/actions/${encodeURIComponent(id)}/confirm`, {
      method: "POST",
      // What comes next is streamed (``streamContinue``): it can outlast the proxy.
      body: JSON.stringify({ payload_hash: payloadHash, acknowledged, wait_for_next: false }),
    }),
  /** Ask the model to rework a pending draft's words; where it goes never changes. */
  reviseAction: (id: string, payloadHash: string, instruction: string, model?: string | null) =>
    request<{ action: ActCard }>(`/act/actions/${encodeURIComponent(id)}/revise`, {
      method: "POST",
      body: JSON.stringify({
        payload_hash: payloadHash,
        instruction,
        model: model && model !== "auto" ? model : null,
      }),
    }),
  editAction: (id: string, payloadHash: string, fields: { title?: string; body?: string }) =>
    request<{ action: ActCard }>(`/act/actions/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ payload_hash: payloadHash, fields }),
    }),
  /** Act in one space: whether it works there, and whether this person (its
   *  owner) may change it. Only within what the company allows. */
  spaceAct: (workspaceId: string) =>
    request<SpaceAct>(`/act/spaces/${encodeURIComponent(workspaceId)}`),
  setSpaceAct: (workspaceId: string, enabled: boolean) =>
    request<SpaceAct>(`/act/spaces/${encodeURIComponent(workspaceId)}`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
  spaceTools: (workspaceId: string) =>
    request<{ servers: SpaceToolServer[] }>(`/act/spaces/${encodeURIComponent(workspaceId)}/tools`),
  /** ``tool`` null switches the whole server off or back on for this space. */
  setSpaceTool: (workspaceId: string, serverId: string, tool: string | null, enabled: boolean) =>
    request<{ servers: SpaceToolServer[] }>(`/act/spaces/${encodeURIComponent(workspaceId)}/tools`, {
      method: "PUT",
      body: JSON.stringify({ server_id: serverId, tool, enabled }),
    }),
  rejectAction: (id: string) =>
    request<{ action: ActCard; next?: ActNext | null }>(`/act/actions/${encodeURIComponent(id)}/reject?wait_for_next=false`, {
      method: "POST",
    }),
  adminActions: () => request<AdminActions>("/admin/actions"),
  setAdminAction: (name: string, enabled: boolean) =>
    request<{ name: string; enabled: boolean }>(`/admin/actions/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
  adminMcp: () =>
    request<{ servers: McpServerAdmin[]; presets: McpPreset[]; sign_in_ready: boolean }>("/admin/mcp"),
  /** Add a connector by its preset key, or any server by name and address. */
  addMcpServer: (server: { preset: string } | { name: string; url: string }) =>
    request<McpServerAdmin>("/admin/mcp", { method: "POST", body: JSON.stringify(server) }),
  /** "all": reads as lookups, every write as a write (each approved on a card). */
  setMcpModes: (id: string, choice: "all" | "off") =>
    request<McpServerAdmin>(`/admin/mcp/${encodeURIComponent(id)}/tools`, {
      method: "PUT",
      body: JSON.stringify({ choice }),
    }),
  refreshMcpServer: (id: string) =>
    request<McpServerAdmin>(`/admin/mcp/${encodeURIComponent(id)}/refresh`, { method: "POST" }),
  setMcpToolMode: (id: string, tool: string, mode: McpTool["mode"]) =>
    request<McpTool>(`/admin/mcp/${encodeURIComponent(id)}/tools/${encodeURIComponent(tool)}`, {
      method: "PUT",
      body: JSON.stringify({ mode }),
    }),
  setMcpServerEnabled: (id: string, enabled: boolean) =>
    request<{ id: string; enabled: boolean }>(`/admin/mcp/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
  deleteMcpServer: (id: string) =>
    request<{ deleted: boolean }>(`/admin/mcp/${encodeURIComponent(id)}`, { method: "DELETE" }),
  myMcp: () => request<{ servers: McpServerMine[] }>("/act/mcp"),
  mcpConnectUrl: (id: string) => `${API_BASE_URL}/act/mcp/${encodeURIComponent(id)}/connect`,
  disconnectMcp: (id: string) =>
    request<{ deleted: boolean }>(`/act/mcp/${encodeURIComponent(id)}/connection`, { method: "DELETE" }),
  getOrgModel: () => request<{ model: OrgModel | null }>("/admin/llm-model"),
  listModelPresets: () =>
    request<{ presets: ModelPreset[] }>("/admin/llm-model/presets"),
  saveOrgModel: (preset: string, model: string, apiKey: string) =>
    request<{ model: OrgModel }>("/admin/llm-model", {
      method: "PUT",
      body: JSON.stringify({ preset, model, api_key: apiKey }),
    }),
  deleteOrgModel: () =>
    request<void>("/admin/llm-model", { method: "DELETE" }),

  checkConnectionHealth: (connectionId: string) =>
    request<{ connection_id: string; provider: string; status: string; needs_reauth: boolean }>(
      `/admin/connections/${connectionId}/health`
    ),
  refreshConnectionScope: (connectionId: string) =>
    request<GitHubScopeResponse>(`/admin/connections/${connectionId}/refresh-scope`, {
      method: "POST",
    }),
  checkConnectionChanges: (connectionId: string) =>
    request<SyncChanges>(`/admin/connections/${connectionId}/changes`),
  triggerIngest: (connectionId: string) =>
    request<{ job_id: string; status: string }>(`/admin/connections/${connectionId}/ingest`, {
      method: "POST",
    }),
  disconnectConnection: (connectionId: string) =>
    request<{ status: string; provider: string; documents_purged: number }>(
      `/admin/connections/${connectionId}`,
      { method: "DELETE" }
    ),

  listJobs: () => request<JobRecord[]>("/admin/jobs"),
  getJob: (jobId: string) => request<JobRecord>(`/admin/jobs/${jobId}`),

  /** In-chat attachments. Extracted text only — the uploaded bytes never
   *  leave the request, so there is no URL to share and nothing to expire. */
  listAttachments: (conversationId: string, workspaceId?: string | null) => {
    const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
    return request<{ attachments: Attachment[] }>(
      `/chat/conversations/${conversationId}/attachments${q}`,
    );
  },

  /** Not `request()`: it pins `Content-Type: application/json`, and a
   *  multipart body needs the BROWSER to set the type so it can put the
   *  boundary in it. Setting it by hand produces a body the server cannot
   *  parse. */
  submitFeedback: (input: {
    conversationId: string;
    question: string;
    rating: 1 | -1;
    answer?: string | null;
    resolvedQuestion?: string | null;
    agent?: string | null;
    reason?: FeedbackReason | null;
    comment?: string | null;
    workspaceId?: string | null;
  }) =>
    request<{ id: string; rating: number }>("/chat/feedback", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: input.conversationId,
        question: input.question,
        rating: input.rating,
        answer: input.answer ?? null,
        resolved_question: input.resolvedQuestion ?? null,
        agent: input.agent ?? null,
        reason: input.reason ?? null,
        comment: input.comment ?? null,
        ...(input.workspaceId ? { workspace_id: input.workspaceId } : {}),
      }),
    }),

  linkedIdentities: () => request<LinkedIdentities>("/account/identities"),

  signInEmails: () => request<SignInEmails>("/account/emails"),

  /** Mails a confirmation link to the NEW address; nothing changes until it is used. */
  requestEmailChange: (email: string) =>
    request<{ status: "sent"; message: string; dev_link: string | null }>("/account/email", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  removePriorEmail: (email: string) =>
    request<{ removed: string }>(`/account/emails/${encodeURIComponent(email)}`, {
      method: "DELETE",
    }),

  /** A full-page navigation, not a fetch: it ends on GitHub's consent screen. */
  githubLinkUrl: () => `${API_BASE_URL}/account/identities/github/link`,

  unlinkIdentity: (identityId: string) =>
    request<{ ok: boolean }>(`/account/identities/${encodeURIComponent(identityId)}`, {
      method: "DELETE",
    }),

  personalMemory: () => request<PersonalMemory>("/account/memory"),

  setMemoryEnabled: (enabled: boolean) =>
    request<{ enabled: boolean }>("/account/memory/settings", {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),

  setOrgMemoryEnabled: (enabled: boolean) =>
    request<{ org_enabled: boolean }>("/account/memory/org", {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),

  pinMemory: (id: string, pinned: boolean) =>
    request<{ id: string; pinned: boolean }>(`/account/memory/${encodeURIComponent(id)}/pin`, {
      method: "POST",
      body: JSON.stringify({ pinned }),
    }),

  forgetMemory: (id: string) =>
    request<{ deleted: string }>(`/account/memory/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),

  clearMemory: () => request<{ deleted: number }>("/account/memory", { method: "DELETE" }),

  /** Company-wide topics (admin), or one space's (its owner). */
  gapTopics: (workspaceId?: string | null) =>
    request<{ topics: GapTopic[] }>(
      workspaceId ? `/workspaces/${encodeURIComponent(workspaceId)}/feedback` : "/admin/feedback",
    ),

  markTopicHandled: (topicId: string, workspaceId?: string | null) =>
    request<{ id: string; handled: boolean }>(
      `${workspaceId ? `/workspaces/${encodeURIComponent(workspaceId)}/feedback` : "/admin/feedback"}/topics/${encodeURIComponent(topicId)}/handled`,
      { method: "POST" },
    ),

  uploadAttachments: async (
    conversationId: string,
    files: File[],
    workspaceId?: string | null,
  ): Promise<UploadResult> => {
    const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
    const form = new FormData();
    // One request for the whole selection: the server accepts what it can and
    // names what it refused, so a scan among four good documents no longer
    // costs the other three.
    for (const file of files) form.append("file", file);
    const response = await fetch(
      `${API_BASE_URL}/chat/conversations/${conversationId}/attachments${q}`,
      { method: "POST", credentials: "include", body: form },
    );
    if (!response.ok) {
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      const parsed = parseApiDetail(body.detail ?? body);
      throw new ApiError(response.status, parsed.message, parsed.code);
    }
    return response.json() as Promise<UploadResult>;
  },

  deleteAttachment: (
    conversationId: string,
    attachmentId: string,
    workspaceId?: string | null,
  ) => {
    const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
    return request<{ deleted: boolean }>(
      `/chat/conversations/${conversationId}/attachments/${attachmentId}${q}`,
      { method: "DELETE" },
    );
  },

  /** Every surviving chat in this scope — retention is the only limit, so
   *  there is nothing alive but off the end of the list. */
  listConversations: (workspaceId?: string | null) => {
    const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
    return request<{
      retention_days: number;
      conversations: ConversationSummary[];
    }>(`/chat/conversations${q}`);
  },

  /** The FULL transcript — possible because the summary fold marks turns as
   *  folded instead of deleting them. */
  getConversation: (conversationId: string, workspaceId?: string | null) => {
    const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
    return request<{ conversation_id: string; turns: ConversationTurn[] }>(
      `/chat/conversations/${conversationId}${q}`,
    );
  },

  deleteConversation: (conversationId: string, workspaceId?: string | null) => {
    const q = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
    return request<{ deleted: boolean }>(`/chat/conversations/${conversationId}${q}`, {
      method: "DELETE",
    });
  },

  createConversation: (workspaceId?: string | null) =>
    request<{ conversation_id: string }>("/chat/conversations", {
      method: "POST",
      body: JSON.stringify(workspaceId ? { workspace_id: workspaceId } : {}),
    }),

  /** Starter chips for the Ask empty state, spanning EVERY connected source.
   *  No `agent` param: Ask is one box, so chips from a single provider would
   *  read as "this box is for Notion" and would hide the other sources from
   *  someone who has never asked about them. `sources` says which providers
   *  actually contributed. */
  chatSuggestions: (workspaceId?: string | null) => {
    const params = new URLSearchParams();
    if (workspaceId) params.set("workspace_id", workspaceId);
    const query = params.toString();
    return request<{
      agent: string | null;
      sources: string[];
      questions: string[];
    }>(`/chat/suggestions${query ? `?${query}` : ""}`);
  },

  /** Chart mode's starters, built by the server from the metrics and fields
   *  this scope actually has -- never copy written into the page. */
  chartStarters: (workspaceId?: string | null, conversationId?: string | null) => {
    const params = new URLSearchParams();
    if (workspaceId) params.set("workspace_id", workspaceId);
    // Starters from the files uploaded to this chat lead the list.
    if (conversationId) params.set("conversation_id", conversationId);
    const q = params.toString();
    return request<{ questions: string[] }>(`/chat/chart-starters${q ? `?${q}` : ""}`);
  },

  chatModels: () =>
    request<{ default: string; default_label: string; models: ModelChoice[] }>(
      "/chat/models",
    ),

  listWorkspaces: () => request<WorkspaceRecord[]>("/workspaces"),
  getWorkspace: (workspaceId: string) =>
    request<WorkspaceDetail>(`/workspaces/${workspaceId}`),
  createWorkspace: (name: string) =>
    request<WorkspaceRecord>("/workspaces", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  deleteWorkspace: (workspaceId: string) =>
    request<{ status: string; workspace_id: string }>(`/workspaces/${workspaceId}`, {
      method: "DELETE",
    }),
  listWorkspaceMembers: (workspaceId: string) =>
    request<WorkspaceMemberRecord[]>(`/workspaces/${workspaceId}/members`),
  inviteWorkspaceMember: (workspaceId: string, email: string) =>
    request<{ status: string; email: string }>(`/workspaces/${workspaceId}/members`, {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  makeWorkspaceOwner: (workspaceId: string, userId: string) =>
    request<{ status: string; user_id: string }>(
      `/workspaces/${workspaceId}/members/${userId}/make-owner`,
      { method: "POST" }
    ),
  removeWorkspaceMember: (workspaceId: string, userId: string) =>
    request<{ status: string; user_id: string }>(
      `/workspaces/${workspaceId}/members/${userId}`,
      { method: "DELETE" }
    ),
  resendWorkspaceInvite: (workspaceId: string, userId: string) =>
    request<{ status: string; email: string }>(
      `/workspaces/${workspaceId}/members/${userId}/resend-invite`,
      { method: "POST" }
    ),
  listWorkspaceConnections: (workspaceId: string) =>
    request<ConnectionRecord[]>(`/workspaces/${workspaceId}/connections`),
  searchWorkspaceConnectionDriveFolders: (workspaceId: string, connectionId: string, q: string) =>
    request<{ folders: DriveFolder[] }>(
      `/workspaces/${workspaceId}/connections/${connectionId}/drive-folders?q=${encodeURIComponent(q)}`
    ),
  setWorkspaceConnectionConfig: (workspaceId: string, connectionId: string, folderUrl: string) =>
    request<ConnectionConfigResponse>(
      `/workspaces/${workspaceId}/connections/${connectionId}/config`,
      { method: "PUT", body: JSON.stringify({ folder_url: folderUrl }) }
    ),
  listWorkspaceConnectionSlackChannels: (workspaceId: string, connectionId: string) =>
    request<{ channels: SlackChannel[] }>(
      `/workspaces/${workspaceId}/connections/${connectionId}/slack-channels`
    ),
  setWorkspaceConnectionSlackChannels: (
    workspaceId: string,
    connectionId: string,
    channelIds: string[]
  ) =>
    request<ConnectionConfigResponse>(
      `/workspaces/${workspaceId}/connections/${connectionId}/config`,
      { method: "PUT", body: JSON.stringify({ channel_ids: channelIds }) }
    ),
  listWorkspaceSlackChannelMembers: (workspaceId: string, connectionId: string, channelId: string) =>
    request<{ members: SlackChannelMember[] }>(
      `/workspaces/${workspaceId}/connections/${connectionId}/slack-channels/${channelId}/members`
    ),
  inviteWorkspaceSlackChannelMembers: (
    workspaceId: string,
    connectionId: string,
    channelId: string,
    emails: string[]
  ) =>
    request<{ invited: string[]; skipped_not_org_member: string[] }>(
      `/workspaces/${workspaceId}/connections/${connectionId}/slack-channels/${channelId}/invite-members`,
      { method: "POST", body: JSON.stringify({ emails }) }
    ),
  checkWorkspaceConnectionHealth: (workspaceId: string, connectionId: string) =>
    request<{ connection_id: string; provider: string; status: string; needs_reauth: boolean }>(
      `/workspaces/${workspaceId}/connections/${connectionId}/health`
    ),
  refreshWorkspaceConnectionScope: (workspaceId: string, connectionId: string) =>
    request<GitHubScopeResponse>(
      `/workspaces/${workspaceId}/connections/${connectionId}/refresh-scope`,
      { method: "POST" }
    ),
  checkWorkspaceConnectionChanges: (workspaceId: string, connectionId: string) =>
    request<SyncChanges>(`/workspaces/${workspaceId}/connections/${connectionId}/changes`),
  triggerWorkspaceIngest: (workspaceId: string, connectionId: string) =>
    request<{ job_id: string; status: string }>(
      `/workspaces/${workspaceId}/connections/${connectionId}/ingest`,
      { method: "POST" }
    ),
  disconnectWorkspaceConnection: (workspaceId: string, connectionId: string) =>
    request<{ status: string; provider: string; documents_purged: number }>(
      `/workspaces/${workspaceId}/connections/${connectionId}`,
      { method: "DELETE" }
    ),
  listWorkspaceJobs: (workspaceId: string) =>
    request<JobRecord[]>(`/workspaces/${workspaceId}/jobs`),
  getWorkspaceJob: (workspaceId: string, jobId: string) =>
    request<JobRecord>(`/workspaces/${workspaceId}/jobs/${jobId}`),
  connectWorkspaceUrl: (workspaceId: string, provider: string) =>
    `${API_BASE_URL}/auth/${provider}/authorize?workspace_id=${workspaceId}`,

  // Scheduled reports. Member-level, not admin — every one of these is scoped
  // to the caller's own user_id server-side, so there is no id to pass for
  // "mine" and no way to reach someone else's.
  listSchedulers: () =>
    request<{ schedulers: SchedulerRecord[] }>("/schedulers").then((r) => r.schedulers),
  listSchedulableConnections: () =>
    request<{ connections: SchedulableConnection[]; spaces: SchedulerSpace[] }>(
      "/schedulers/connections"
    ),
  /**
   * Create a schedule. No provider: the server classifies it from the prompt
   * and the created record reports which one it landed on. Sending one would
   * be the old dropdown, and there is nowhere left in the UI to pick it.
   */
  createScheduler: (
    frequency: string,
    prompt: string,
    workspaceId: string | null = null
  ) =>
    request<SchedulerRecord>("/schedulers", {
      method: "POST",
      body: JSON.stringify({
        frequency,
        prompt,
        workspace_id: workspaceId,
      }),
    }),
  updateScheduler: (
    schedulerId: string,
    changes: { frequency?: string; prompt?: string }
  ) =>
    request<SchedulerRecord>(`/schedulers/${schedulerId}`, {
      method: "PATCH",
      body: JSON.stringify(changes),
    }),
  deleteScheduler: (schedulerId: string) =>
    request<void>(`/schedulers/${schedulerId}`, { method: "DELETE" }),
  listReports: () =>
    request<{ reports: ReportRow[] }>("/schedulers/reports").then((r) => r.reports),
  getReport: (reportId: string) =>
    request<ReportDetail>(`/schedulers/reports/${reportId}`),
  /** Send the whole conversation each turn — the endpoint is stateless. */
  schedulerSetupChat: (messages: SetupChatMessage[]) =>
    request<SetupChatResponse>("/schedulers/setup-chat", {
      method: "POST",
      body: JSON.stringify({ messages }),
    }),
  // Charts. Member-level like schedulers: a chart aggregates rows the caller
  // can already retrieve in prose. `scope` is a workspace id, or omitted for
  // the company - a space the caller is not in returns 403, never an empty
  // chart, because empty reads as "nothing happened there".
  listInsightScopes: () =>
    request<{ scopes: InsightScope[] }>("/insights/scopes").then((r) => r.scopes),
  getInsightDashboard: (scope: string | null, period: string) =>
    request<InsightDashboard>(
      `/insights/dashboard?period=${encodeURIComponent(period)}` +
        (scope ? `&scope=${encodeURIComponent(scope)}` : "")
    ),
  // One turn, no conversation. The model only SELECTS a pre-defined chart -
  // it never writes SQL and never emits a number, so the worst case is a
  // refusal that names what is available.
  askForChart: (question: string, scope: string | null) =>
    request<InsightAskResult>("/insights/ask", {
      method: "POST",
      body: JSON.stringify({ question, scope }),
    }),
  listInsightPins: () =>
    request<{ pins: InsightPin[] }>("/insights/pins").then((r) => r.pins),
  pinChart: (
    metric: string,
    groupBy: string | null,
    period: string,
    scope: string | null
  ) =>
    request<{ id: string | null }>("/insights/pins", {
      method: "POST",
      body: JSON.stringify({ metric, group_by: groupBy, period, scope }),
    }),
  unpinChart: (pinId: string) =>
    request<void>(`/insights/pins/${pinId}`, { method: "DELETE" }),
  getConnectorFreshness: (scope: string | null) =>
    request<{ connectors: ConnectorFreshness[] }>(
      `/insights/freshness${scope ? `?scope=${encodeURIComponent(scope)}` : ""}`
    ).then((r) => r.connectors),
};

export { API_BASE_URL };
