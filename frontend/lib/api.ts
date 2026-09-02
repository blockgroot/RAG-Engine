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
  slack_ready: boolean;
  linear_ready: boolean;
  notion_ready: boolean;
  drive_ready: boolean;
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
  account_login?: string;
  repository_selection?: "all" | "selected";
  repos?: GitHubRepoRef[];
  channel_ids?: string[];
  channel_names?: Record<string, string>;
}

export interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
  is_member: boolean;
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
}

export interface ConnectionConfigResponse {
  connection_id: string;
  provider: string;
  config: ConnectionSourceConfig;
  folder_changed?: boolean;
  channels_changed?: boolean;
  documents_purged?: number;
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
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface MagicLinkResponse {
  status: "sent" | "no_account";
  message: string;
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
  frequency: "weekly" | "monthly";
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
  frequency: "weekly" | "monthly";
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
}

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
};

export { API_BASE_URL };
