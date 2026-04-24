import type {
  AgentLineage,
  AgentListResponse,
  AgentMemoryResponse,
  AgentSpec,
  AgentToolsResponse,
  AgentVersion,
  AgentVersionListResponse,
  AuditFilters,
  AuditListResponse,
  AuditStatus,
  ChatRequest,
  ChatResponse,
  CreateAgentRequest,
  CreateTeamRequest,
  CreateTeamVersionRequest,
  CreateVersionRequest,
  CredentialStatusResponse,
  ForkRequest,
  HealthResponse,
  PolicyEngineInfo,
  PolicyEngineListResponse,
  PolicyInfo,
  PolicyListResponse,
  ProvidersResponse,
  SessionHistoryResponse,
  TeamLineage,
  TeamListResponse,
  TeamRunRequest,
  TeamRunResponse,
  TeamSpec,
  TeamVersion,
  TeamVersionListResponse,
  ToolCatalogResponse,
  UpdateAgentRequest,
  UpdateTeamRequest,
  WhoAmIResponse,
} from "./types";

class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

// ── Auth token injection ──────────────────────────────────────────
// The AuthProvider sets this so all API calls include the Bearer token.
let _authToken = "";
export function setApiAuthToken(token: string) {
  _authToken = token;
}

function authHeaders(): Record<string, string> {
  if (_authToken) {
    return { Authorization: `Bearer ${_authToken}` };
  }
  return {};
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }
  return res.json();
}

/** Create a ReadableStream from a GET SSE endpoint (for reconnection). */
function sseGetStream(url: string, signal?: AbortSignal): ReadableStream<string> {
  return new ReadableStream({
    async start(controller) {
      const res = await fetch(url, {
        headers: { ...authHeaders() },
        signal,
      });
      if (!res.ok || !res.body) {
        const err = await res.text().catch(() => res.statusText);
        controller.enqueue(`data: ${JSON.stringify({ type: "error", detail: err })}\n\n`);
        controller.close();
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          controller.enqueue(decoder.decode(value, { stream: true }));
        }
      } finally {
        controller.close();
      }
    },
  });
}

/** Serialize AuditFilters into URLSearchParams — arrays become repeated keys. */
function appendAuditFilters(qs: URLSearchParams, filters: AuditFilters): void {
  for (const [k, v] of Object.entries(filters)) {
    if (v === undefined || v === "") continue;
    if (Array.isArray(v)) {
      for (const item of v) qs.append(k, item);
    } else {
      qs.set(k, v);
    }
  }
}

/** Create a ReadableStream that POSTs to an SSE endpoint and pipes the response. */
function sseStream(url: string, body: string, signal?: AbortSignal): ReadableStream<string> {
  return new ReadableStream({
    async start(controller) {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body,
        signal,
      });
      if (!res.ok || !res.body) {
        const err = await res.text().catch(() => res.statusText);
        controller.enqueue(`data: ${JSON.stringify({ type: "error", detail: err })}\n\n`);
        controller.close();
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          controller.enqueue(decoder.decode(value, { stream: true }));
        }
      } finally {
        controller.close();
      }
    },
  });
}

export const api = {
  // Health — liveness only. `/readyz` is an anonymous kubelet probe;
  // the dashboard uses `/api/v1/providers/status` below for per-provider
  // data so audit events are attributed to the logged-in user.
  health: () => request<HealthResponse>("/healthz"),

  // Authenticated provider status (uses user credentials for healthchecks).
  // Sole dashboard source for the Provider chips + Readiness badge —
  // client-side reducer collapses the checks dict into a single
  // ok/degraded value.
  providerStatus: () =>
    request<{ checks: Record<string, string> }>("/api/v1/providers/status"),

  // Providers
  providers: () => request<ProvidersResponse>("/api/v1/providers"),

  // Agents
  listAgents: () =>
    request<AgentListResponse>("/api/v1/agents").then((r) => r.agents),
  getAgent: (name: string) => request<AgentSpec>(`/api/v1/agents/${name}`),
  createAgent: (data: CreateAgentRequest) =>
    request<AgentSpec>("/api/v1/agents", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateAgent: (name: string, data: UpdateAgentRequest) =>
    request<AgentSpec>(`/api/v1/agents/${name}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteAgent: (name: string) =>
    request<{ status: string }>(`/api/v1/agents/${name}`, {
      method: "DELETE",
    }),
  chat: (name: string, data: ChatRequest) =>
    request<ChatResponse>(`/api/v1/agents/${name}/chat`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  exportAgent: async (name: string) => {
    const res = await fetch(`/api/v1/agents/${name}/export`, {
      headers: { ...authHeaders() },
    });
    if (!res.ok) throw new Error(`Export failed: ${res.statusText}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}.py`;
    a.click();
    URL.revokeObjectURL(url);
  },
  getAgentTools: (name: string) =>
    request<AgentToolsResponse>(`/api/v1/agents/${name}/tools`),
  getToolCatalog: () =>
    request<ToolCatalogResponse>("/api/v1/agents/tool-catalog"),
  chatStream: (name: string, data: ChatRequest, signal?: AbortSignal): ReadableStream<string> =>
    sseStream(`/api/v1/agents/${name}/chat/stream`, JSON.stringify(data), signal),
  listSessions: (name: string) =>
    request<{ agent_name: string; sessions: Array<{ session_id: string; [key: string]: unknown }> }>(
      `/api/v1/agents/${name}/sessions`,
    ),
  getSessionHistory: (name: string, sessionId: string) =>
    request<SessionHistoryResponse>(`/api/v1/agents/${name}/sessions/${sessionId}`),
  getSessionStatus: (name: string, sessionId: string) =>
    request<{ status: string }>(`/api/v1/agents/${name}/sessions/${sessionId}/status`),
  deleteSession: (name: string, sessionId: string) =>
    request<{ status: string }>(`/api/v1/agents/${name}/sessions/${sessionId}`, { method: "DELETE" }),
  cleanupSessions: (name: string, keep = 5) =>
    request<{ deleted: number; kept: number }>(`/api/v1/agents/${name}/sessions/cleanup?keep=${keep}`, { method: "POST" }),
  cancelSessionRun: (name: string, sessionId: string) =>
    request<{ status: string }>(`/api/v1/agents/${name}/sessions/${sessionId}/run`, { method: "DELETE" }),
  reconnectSessionStream: (name: string, sessionId: string, signal?: AbortSignal): ReadableStream<string> =>
    sseGetStream(`/api/v1/agents/${name}/sessions/${sessionId}/stream`, signal),
  getAgentMemory: (name: string, sessionId?: string) => {
    const params = sessionId ? `?session_id=${sessionId}` : "";
    return request<AgentMemoryResponse>(
      `/api/v1/agents/${name}/memory${params}`,
    );
  },

  // Teams
  listTeams: () =>
    request<TeamListResponse>("/api/v1/teams").then((r) => r.teams),
  getTeam: (name: string) => request<TeamSpec>(`/api/v1/teams/${name}`),
  createTeam: (data: CreateTeamRequest) =>
    request<TeamSpec>("/api/v1/teams", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateTeam: (name: string, data: UpdateTeamRequest) =>
    request<TeamSpec>(`/api/v1/teams/${name}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteTeam: (name: string) =>
    request<{ status: string }>(`/api/v1/teams/${name}`, {
      method: "DELETE",
    }),
  exportTeam: async (name: string) => {
    const res = await fetch(`/api/v1/teams/${name}/export`, {
      headers: { ...authHeaders() },
    });
    if (!res.ok) throw new Error(`Export failed: ${res.statusText}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}.py`;
    a.click();
    URL.revokeObjectURL(url);
  },
  runTeam: (name: string, data: TeamRunRequest) =>
    request<TeamRunResponse>(`/api/v1/teams/${name}/run`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  listTeamRuns: (name: string) =>
    request<{ team_name: string; runs: Array<{ team_run_id: string; status: string }> }>(
      `/api/v1/teams/${name}/runs`,
    ),
  deleteTeamRun: (name: string, runId: string) =>
    request<{ status: string }>(`/api/v1/teams/${name}/runs/${runId}`, { method: "DELETE" }),
  reconnectTeamStream: (name: string, runId: string, signal?: AbortSignal): ReadableStream<string> =>
    sseGetStream(`/api/v1/teams/${name}/runs/${runId}/stream`, signal),
  cancelTeamRun: (name: string, runId: string) =>
    request<{ status: string }>(`/api/v1/teams/${name}/runs/${runId}/cancel`, { method: "DELETE" }),
  getTeamRunStatus: (name: string, runId: string) =>
    request<{ status: string }>(`/api/v1/teams/${name}/runs/${runId}/status`),
  getTeamRun: (name: string, runId: string) =>
    request<{ team_run_id: string; team_name: string; status: string; tasks: Array<{ id: string; title: string; status: string; assigned_to: string; suggested_worker: string; result: string }>; tasks_created: number; tasks_completed: number }>(
      `/api/v1/teams/${name}/runs/${runId}`,
    ),
  getTeamRunEvents: (name: string, runId: string) =>
    request<{ team_run_id: string; status: string; events: Array<Record<string, unknown>> }>(
      `/api/v1/teams/${name}/runs/${runId}/events`,
    ),
  retryTeamTask: (name: string, runId: string, taskId: string, signal?: AbortSignal): ReadableStream<string> =>
    sseStream(`/api/v1/teams/${name}/runs/${runId}/tasks/${taskId}/retry`, "{}", signal),
  runTeamStream: (name: string, data: TeamRunRequest, signal?: AbortSignal): ReadableStream<string> =>
    sseStream(`/api/v1/teams/${name}/run/stream`, JSON.stringify(data), signal),

  // Credentials
  credentialStatus: () =>
    request<CredentialStatusResponse>("/api/v1/credentials/status"),
  readCredentials: () =>
    request<{ attributes: Record<string, string>; services: Record<string, Record<string, string>> }>(
      "/api/v1/credentials",
    ),
  writeCredentials: (data: { attributes: Record<string, string> }) =>
    request<{ status: string }>("/api/v1/credentials", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteCredential: (key: string) =>
    request<{ status: string }>(`/api/v1/credentials/${encodeURIComponent(key)}`, {
      method: "DELETE",
    }),

  // Policy
  listPolicyEngines: () =>
    request<PolicyEngineListResponse>("/api/v1/policy/engines"),
  createPolicyEngine: (name: string, description = "") =>
    request<PolicyEngineInfo>("/api/v1/policy/engines", {
      method: "POST",
      body: JSON.stringify({ name, description }),
    }),
  deletePolicyEngine: (engineId: string) =>
    fetch(`/api/v1/policy/engines/${engineId}`, { method: "DELETE" }),
  listPolicies: (engineId: string) =>
    request<PolicyListResponse>(
      `/api/v1/policy/engines/${engineId}/policies`,
    ),
  createPolicy: (engineId: string, policyBody: string, description = "") =>
    request<PolicyInfo>(`/api/v1/policy/engines/${engineId}/policies`, {
      method: "POST",
      body: JSON.stringify({ policy_body: policyBody, description }),
    }),
  deletePolicy: (engineId: string, policyId: string) =>
    fetch(`/api/v1/policy/engines/${engineId}/policies/${policyId}`, {
      method: "DELETE",
    }),

  // Auth / whoami
  whoami: () => request<WhoAmIResponse>("/api/v1/auth/whoami"),

  // Audit (admin-only)
  auditStatus: () => request<AuditStatus>("/api/v1/audit/status"),
  listAuditEvents: (filters: AuditFilters = {}, start = "-", end = "+", count = 100) => {
    const qs = new URLSearchParams({ start, end, count: String(count) });
    appendAuditFilters(qs, filters);
    return request<AuditListResponse>(`/api/v1/audit/events?${qs}`);
  },
  // Live tail is always broad — the UI filters client-side so that
  // changing filters doesn't drop buffered events on reconnect.
  streamAuditEvents: (signal?: AbortSignal) =>
    sseGetStream("/api/v1/audit/events/stream", signal),

  // ── Agent versioning / fork / lineage ───────────────────────────
  // ``name`` may be bare (``"researcher"``) or qualified (``"alice:researcher"``).
  listAgentVersions: (name: string) =>
    request<AgentVersionListResponse>(
      `/api/v1/agents/${encodeURIComponent(name)}/versions`,
    ).then((r) => r.versions),
  getAgentVersion: (name: string, versionId: string) =>
    request<AgentVersion>(
      `/api/v1/agents/${encodeURIComponent(name)}/versions/${versionId}`,
    ),
  createAgentVersion: (name: string, data: CreateVersionRequest) =>
    request<AgentVersion>(
      `/api/v1/agents/${encodeURIComponent(name)}/versions`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  proposeAgentVersion: (name: string, versionId: string) =>
    request<AgentVersion>(
      `/api/v1/agents/${encodeURIComponent(name)}/versions/${versionId}/propose`,
      { method: "POST" },
    ),
  approveAgentVersion: (name: string, versionId: string) =>
    request<AgentVersion>(
      `/api/v1/agents/${encodeURIComponent(name)}/versions/${versionId}/approve`,
      { method: "POST" },
    ),
  rejectAgentVersion: (name: string, versionId: string, reason: string) =>
    request<AgentVersion>(
      `/api/v1/agents/${encodeURIComponent(name)}/versions/${versionId}/reject`,
      { method: "POST", body: JSON.stringify({ reason }) },
    ),
  deployAgentVersion: (name: string, versionId: string) =>
    request<AgentVersion>(
      `/api/v1/agents/${encodeURIComponent(name)}/versions/${versionId}/deploy`,
      { method: "POST" },
    ),
  forkAgent: (name: string, data: ForkRequest = {}) =>
    request<AgentVersion>(
      `/api/v1/agents/${encodeURIComponent(name)}/fork`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  getAgentLineage: (name: string) =>
    request<AgentLineage>(`/api/v1/agents/${encodeURIComponent(name)}/lineage`),
  listPendingAgentProposals: () =>
    request<AgentVersionListResponse>("/api/v1/admin/agents/proposals").then(
      (r) => r.versions,
    ),

  // ── Team versioning / fork / lineage ────────────────────────────
  listTeamVersions: (name: string) =>
    request<TeamVersionListResponse>(
      `/api/v1/teams/${encodeURIComponent(name)}/versions`,
    ).then((r) => r.versions),
  getTeamVersion: (name: string, versionId: string) =>
    request<TeamVersion>(
      `/api/v1/teams/${encodeURIComponent(name)}/versions/${versionId}`,
    ),
  createTeamVersion: (name: string, data: CreateTeamVersionRequest) =>
    request<TeamVersion>(
      `/api/v1/teams/${encodeURIComponent(name)}/versions`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  proposeTeamVersion: (name: string, versionId: string) =>
    request<TeamVersion>(
      `/api/v1/teams/${encodeURIComponent(name)}/versions/${versionId}/propose`,
      { method: "POST" },
    ),
  approveTeamVersion: (name: string, versionId: string) =>
    request<TeamVersion>(
      `/api/v1/teams/${encodeURIComponent(name)}/versions/${versionId}/approve`,
      { method: "POST" },
    ),
  rejectTeamVersion: (name: string, versionId: string, reason: string) =>
    request<TeamVersion>(
      `/api/v1/teams/${encodeURIComponent(name)}/versions/${versionId}/reject`,
      { method: "POST", body: JSON.stringify({ reason }) },
    ),
  deployTeamVersion: (name: string, versionId: string) =>
    request<TeamVersion>(
      `/api/v1/teams/${encodeURIComponent(name)}/versions/${versionId}/deploy`,
      { method: "POST" },
    ),
  forkTeam: (name: string, data: ForkRequest = {}) =>
    request<TeamVersion>(
      `/api/v1/teams/${encodeURIComponent(name)}/fork`,
      { method: "POST", body: JSON.stringify(data) },
    ),
  getTeamLineage: (name: string) =>
    request<TeamLineage>(`/api/v1/teams/${encodeURIComponent(name)}/lineage`),
  listPendingTeamProposals: () =>
    request<TeamVersionListResponse>("/api/v1/admin/teams/proposals").then(
      (r) => r.versions,
    ),
};

/** Check if an error message indicates missing user credentials. */
export function isCredentialError(message: string): boolean {
  return (
    message.includes("allow_server_credentials") ||
    message.includes("X-Cred-") ||
    message.includes("X-AWS-") ||
    message.includes("server credential fallback is disabled") ||
    message.includes("No AWS credentials")
  );
}

export { ApiError };
