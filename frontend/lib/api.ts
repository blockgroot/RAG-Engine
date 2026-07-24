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
}

export interface DomainRecord {
  id: string;
  domain: string;
  verified: boolean;
  auto_join_enabled: boolean;
}

export interface DomainRegistration {
  domain_id: string;
  dns_record_name: string;
  dns_record_value: string;
  instructions: string;
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

export const api = {
  requestMagicLink: (email: string) =>
    request<{ message: string }>("/auth/magic-link", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  me: () => request<Me>("/me"),

  connectUrl: (provider: string) => `${API_BASE_URL}/auth/${provider}/authorize`,

  listDomains: () => request<DomainRecord[]>("/admin/domains"),
  registerDomain: (domain: string) =>
    request<DomainRegistration>("/admin/domains", {
      method: "POST",
      body: JSON.stringify({ domain }),
    }),
  verifyDomain: (domainId: string) =>
    request<{ verified: boolean }>(`/admin/domains/${domainId}/verify`, { method: "POST" }),
  setAutoJoin: (domainId: string, enabled: boolean) =>
    request<{ auto_join_enabled: boolean }>(`/admin/domains/${domainId}/auto-join`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),

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
