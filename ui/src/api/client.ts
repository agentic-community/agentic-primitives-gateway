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

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }
  return res.json();
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
  chatStream: (name: string, data: ChatRequest): ReadableStream<string> => {
    const body = JSON.stringify(data);
    // Return a ReadableStream that the caller can consume
    return new ReadableStream({
      async start(controller) {
        const res = await fetch(`/api/v1/agents/${name}/chat/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
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
  },
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
  runTeamStream: (name: string, data: TeamRunRequest): ReadableStream<string> => {
    const body = JSON.stringify(data);
    return new ReadableStream({
      async start(controller) {
        const res = await fetch(`/api/v1/teams/${name}/run/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
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
  },

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
