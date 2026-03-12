export interface PrimitiveConfig {
  enabled: boolean;
  tools: string[] | null;
  namespace: string | null;
}

export interface HooksConfig {
  auto_memory: boolean;
  auto_trace: boolean;
}

export interface AgentSpec {
  name: string;
  description: string;
  model: string;
  system_prompt: string;
  primitives: Record<string, PrimitiveConfig>;
  hooks: HooksConfig;
  provider_overrides: Record<string, string>;
  max_turns: number;
  temperature: number;
  max_tokens: number | null;
  owner_id: string;
  shared_with: string[];
  checkpointing_enabled: boolean;
}

export interface AgentListResponse {
  agents: AgentSpec[];
}

export interface CreateAgentRequest {
  name: string;
  description?: string;
  model: string;
  system_prompt?: string;
  primitives?: Record<string, PrimitiveConfig>;
  hooks?: HooksConfig;
  provider_overrides?: Record<string, string>;
  max_turns?: number;
  temperature?: number;
  max_tokens?: number | null;
  shared_with?: string[];
  checkpointing_enabled?: boolean;
}

export interface UpdateAgentRequest {
  description?: string;
  model?: string;
  system_prompt?: string;
  primitives?: Record<string, PrimitiveConfig>;
  hooks?: HooksConfig;
  provider_overrides?: Record<string, string>;
  max_turns?: number;
  temperature?: number;
  max_tokens?: number | null;
  shared_with?: string[];
  checkpointing_enabled?: boolean;
}

export interface ChatRequest {
  message: string;
  session_id?: string;
}

export interface ChatResponse {
  response: string;
  session_id: string;
  agent_name: string;
  turns_used: number;
  tools_called: string[];
  metadata: Record<string, unknown>;
}

export interface HealthResponse {
  status: "ok" | "degraded" | "error";
}

export interface ReadinessResponse {
  status: "ok" | "degraded" | "error";
  checks: Record<string, boolean>;
  config_reload_error?: string;
}

export interface ProviderInfo {
  default: string;
  available: string[];
}

export type ProvidersResponse = Record<string, ProviderInfo>;

// Policy types

export interface PolicyEngineInfo {
  policy_engine_id: string;
  name: string;
  description: string;
  status: string;
  created_at: string;
}

export interface PolicyEngineListResponse {
  policy_engines: PolicyEngineInfo[];
  next_token: string | null;
}

export interface PolicyInfo {
  policy_id: string;
  policy_engine_id: string;
  definition: string;
  description: string;
  created_at: string;
}

export interface PolicyListResponse {
  policies: PolicyInfo[];
  next_token: string | null;
}

// Streaming event types

export type StreamEvent =
  | { type: "stream_start"; session_id: string }
  | { type: "token"; content: string }
  | { type: "tool_call_start"; name: string; id: string }
  | { type: "tool_call_result"; name: string; id: string; result: string }
  | { type: "sub_agent_token"; agent: string; content: string }
  | { type: "sub_agent_tool"; agent: string; name: string }
  | { type: "done"; response: string; session_id: string; agent_name: string; turns_used: number; tools_called: string[]; artifacts?: StreamArtifact[]; metadata: Record<string, unknown> };

export interface StreamArtifact {
  tool_name: string;
  code: string;
  language: string;
  output: string;
}

// Tool catalog types

export interface CatalogToolInfo {
  name: string;
  description: string;
}

export interface ToolCatalogResponse {
  primitives: Record<string, CatalogToolInfo[]>;
}

// Agent tools types

export interface AgentToolInfo {
  name: string;
  description: string;
  primitive: string;
  provider: string;
}

export interface AgentToolsResponse {
  agent_name: string;
  tools: AgentToolInfo[];
}

// Team types

export interface TeamSpec {
  name: string;
  description: string;
  planner: string;
  synthesizer: string;
  workers: string[];
  max_concurrent: number | null;
  global_max_turns: number;
  global_timeout_seconds: number;
  shared_memory_namespace: string | null;
  owner_id: string;
  shared_with: string[];
  checkpointing_enabled: boolean;
}

export interface TeamListResponse {
  teams: TeamSpec[];
}

export interface CreateTeamRequest {
  name: string;
  description?: string;
  planner: string;
  synthesizer: string;
  workers: string[];
  max_concurrent?: number | null;
  global_max_turns?: number;
  global_timeout_seconds?: number;
  shared_with?: string[];
  checkpointing_enabled?: boolean;
}

export interface UpdateTeamRequest {
  description?: string;
  planner?: string;
  synthesizer?: string;
  workers?: string[];
  max_concurrent?: number | null;
  global_max_turns?: number;
  global_timeout_seconds?: number;
  shared_with?: string[];
  checkpointing_enabled?: boolean;
}

export interface TeamRunRequest {
  message: string;
}

export interface TeamRunResponse {
  response: string;
  team_run_id: string;
  team_name: string;
  phase: string;
  tasks_created: number;
  tasks_completed: number;
  workers_used: string[];
  metadata: Record<string, unknown>;
}

// Team stream event types

export type TeamStreamEvent =
  | { type: "team_start"; team_run_id: string; team_name: string }
  | { type: "phase_change"; phase: string }
  | { type: "tasks_created"; count: number; tasks: { id: string; title: string; priority: number; suggested_worker?: string }[] }
  | { type: "task_created"; task: { id: string; title: string; priority: number; suggested_worker?: string } }
  | { type: "task_claimed"; agent: string; task_id: string; title: string }
  | { type: "task_completed"; agent: string; task_id: string; result: string }
  | { type: "task_failed"; agent: string; task_id: string; error: string }
  | { type: "worker_start"; agent: string }
  | { type: "worker_done"; agent: string }
  | { type: "worker_error"; agent: string; error: string }
  | { type: "agent_token"; agent: string; content: string; task_id?: string }
  | { type: "agent_tool"; agent: string; name: string; task_id?: string }
  | { type: "done"; response: string; team_run_id: string; team_name: string; phase: string; tasks_created: number; tasks_completed: number; workers_used: string[] }
  | { type: "cancelled" };

// Agent memory types

export interface MemoryStoreInfo {
  namespace: string;
  memory_count: number;
  memories: { key: string; content: string; updated_at: string }[];
}

export interface AgentMemoryResponse {
  agent_name: string;
  memory_enabled: boolean;
  namespace: string;
  stores: MemoryStoreInfo[];
}

export interface SessionHistoryResponse {
  agent_name: string;
  session_id: string;
  messages: { role: string; content: string }[];
}
