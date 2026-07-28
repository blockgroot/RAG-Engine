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
}

export interface MemberRecord {
  id: string;
  email: string;
  role: "admin" | "member";
  created_at: string;
}

export interface ConnectionRecord {
  id: string;
  provider: "notion" | "google" | "github";
  external_workspace_name: string | null;
  created_at: string;
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

export const api = {
  signup: (email: string, companyName: string) =>
    request<MagicLinkResponse>("/auth/signup", {
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
  triggerIngest: (connectionId: string) =>
    request<{ job_id: string; status: string }>(`/admin/connections/${connectionId}/ingest`, {
      method: "POST",
    }),

  listJobs: () => request<JobRecord[]>("/admin/jobs"),
  getJob: (jobId: string) => request<JobRecord>(`/admin/jobs/${jobId}`),

  createConversation: () =>
    request<{ conversation_id: string }>("/chat/conversations", { method: "POST" }),
};

export { API_BASE_URL };
