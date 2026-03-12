import type {
  AgentListResponse,
  AgentMemoryResponse,
  AgentSpec,
  AgentToolsResponse,
  ChatRequest,
  ChatResponse,
  CreateAgentRequest,
  CreateTeamRequest,
  HealthResponse,
  PolicyEngineInfo,
  PolicyEngineListResponse,
  PolicyInfo,
  PolicyListResponse,
  ProvidersResponse,
  ReadinessResponse,
  SessionHistoryResponse,
  TeamListResponse,
  TeamRunRequest,
  TeamRunResponse,
  TeamSpec,
  ToolCatalogResponse,
  UpdateAgentRequest,
  UpdateTeamRequest,
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
  // Health
  health: () => request<HealthResponse>("/healthz"),
  readiness: async (): Promise<ReadinessResponse> => {
    // readyz returns 503 when providers are degraded, but the body still
    // contains useful check data — parse it regardless of status code.
    const res = await fetch("/readyz", {
      headers: { "Content-Type": "application/json" },
    });
    return res.json();
  },

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
  cancelSessionRun: (name: string, sessionId: string) =>
    request<{ status: string }>(`/api/v1/agents/${name}/sessions/${sessionId}/run`, { method: "DELETE" }),
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
  runTeamStream: (name: string, data: TeamRunRequest, signal?: AbortSignal): ReadableStream<string> =>
    sseStream(`/api/v1/teams/${name}/run/stream`, JSON.stringify(data), signal),

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
};

export { ApiError };
