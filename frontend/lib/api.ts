/**
 * Typed fetch wrapper for the FastAPI backend.
 *
 * `credentials: "include"` on every call — the session lives in an httpOnly
 * cookie, never in JS-accessible storage, so there is no token to smuggle out
 * via an XSS payload. The backend's own CORS config (API_CORS_ORIGINS) is
 * what actually authorizes cross-origin credentialed requests; this client
 * has no separate auth mechanism to get wrong.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
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
    throw new ApiError(response.status, body.detail || "Request failed");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export interface Me {
  user_id: string;
  org_id: string;
  org_name: string | null;
  role: "admin" | "member";
  has_connection: boolean;
  /** True once any document row exists (may still be mid-ingest). */
  has_documents: boolean;
  /** Queued or running ingestion job for this org. */
  sync_in_progress: boolean;
  latest_job_status: string | null;
  latest_doc_count: number | null;
  /** Safe to open Ask only after a full ingest job has succeeded. */
  ready_to_ask: boolean;
  /**
   * True when this org has an org-wide GitHub connection, so the chat UI can
   * offer its "Code" tab. Reported on /me rather than read from
   * /admin/connections because that route is admin-only and members must be
   * able to ask repository questions too. A boolean only -- repository names
   * stay behind require_admin.
   *
   * Note this is independent of `ready_to_ask`: GitHub answers are read live
   * and need no ingest, so the Code tab works even before any policy sync.
   */
  github_connected: boolean;
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
}

/** One repository a GitHub installation is authorized to read. */
export interface GitHubRepoRef {
  full_name: string;
  description: string | null;
  topics: string[];
}

export interface ConnectionSourceConfig {
  // Google Drive: the folder the admin picked.
  folder_id?: string;
  folder_name?: string;
  // GitHub: what the admin actually authorized on GitHub's install screen.
  // "all" means every repo of the connected account (including ones created
  // later); "selected" means exactly `repos`. Stored rather than assumed --
  // connecting GitHub does not by itself grant everything.
  installation_id?: string;
  account_login?: string;
  repository_selection?: "all" | "selected";
  repos?: GitHubRepoRef[];
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
  provider: "notion" | "google" | "github";
  external_workspace_name: string | null;
  created_at: string;
  /** Non-secret ingestion scope (e.g. Google Drive folder). */
  source_config?: ConnectionSourceConfig | null;
}

export interface ConnectionConfigResponse {
  connection_id: string;
  provider: string;
  config: ConnectionSourceConfig;
}

/** One Drive folder the connected account can see (folder-picker dropdown). */
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
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface MagicLinkResponse {
  message: string;
  // Only ever set when the backend has no real email sender configured
  // (EMAIL_SENDER=console, i.e. local dev) — lets the UI offer a direct link
  // instead of "go check the server terminal". Always null once SMTP is
  // configured for a real deployment.
  dev_link: string | null;
}

export interface SignupResponse {
  // No dev_link: nothing is generated to sign in with yet — signup only
  // queues a pending org_signup_requests row for platform-owner review.
  message: string;
}

/** A sub-workspace ("workspace within a workspace") the caller is a member of. */
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
}

export interface WorkspaceMemberRecord {
  email: string;
  role: "owner" | "member";
  joined_at: string;
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

  connectUrl: (provider: string) => `${API_BASE_URL}/auth/${provider}/authorize`,

  listMembers: () => request<MemberRecord[]>("/admin/members"),
  inviteMember: (email: string) =>
    request<{ id: string; email: string; role: string; dev_link?: string | null }>(
      "/admin/members",
      {
        method: "POST",
        body: JSON.stringify({ email }),
      }
    ),

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
  /**
   * Re-read which repositories a GitHub installation may see.
   *
   * The GitHub analogue of Drive's "check for changes" -- but for *scope*, not
   * content. Nothing is ever ingested from GitHub, so there is no content to
   * sync; the only thing that can drift is which repos the admin authorized on
   * GitHub's own install screen.
   */
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

  listJobs: () => request<JobRecord[]>("/admin/jobs"),
  getJob: (jobId: string) => request<JobRecord>(`/admin/jobs/${jobId}`),

  createConversation: (workspaceId?: string | null) =>
    request<{ conversation_id: string }>("/chat/conversations", {
      method: "POST",
      body: JSON.stringify(workspaceId ? { workspace_id: workspaceId } : {}),
    }),

  // --- Workspace-within-a-Workspace ---

  listWorkspaces: () => request<WorkspaceRecord[]>("/workspaces"),
  getWorkspace: (workspaceId: string) =>
    request<WorkspaceDetail>(`/workspaces/${workspaceId}`),
  createWorkspace: (name: string) =>
    request<WorkspaceRecord>("/workspaces", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  listWorkspaceMembers: (workspaceId: string) =>
    request<WorkspaceMemberRecord[]>(`/workspaces/${workspaceId}/members`),
  inviteWorkspaceMember: (workspaceId: string, email: string) =>
    request<{ status: string; email: string }>(`/workspaces/${workspaceId}/members`, {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
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
  checkWorkspaceConnectionChanges: (workspaceId: string, connectionId: string) =>
    request<SyncChanges>(`/workspaces/${workspaceId}/connections/${connectionId}/changes`),
  triggerWorkspaceIngest: (workspaceId: string, connectionId: string) =>
    request<{ job_id: string; status: string }>(
      `/workspaces/${workspaceId}/connections/${connectionId}/ingest`,
      { method: "POST" }
    ),
  listWorkspaceJobs: (workspaceId: string) =>
    request<JobRecord[]>(`/workspaces/${workspaceId}/jobs`),
  getWorkspaceJob: (workspaceId: string, jobId: string) =>
    request<JobRecord>(`/workspaces/${workspaceId}/jobs/${jobId}`),
  connectWorkspaceUrl: (workspaceId: string, provider: string) =>
    `${API_BASE_URL}/auth/${provider}/authorize?workspace_id=${workspaceId}`,
};

export { API_BASE_URL };
