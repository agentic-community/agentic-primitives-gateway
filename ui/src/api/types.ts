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
  | { type: "done"; response: string; session_id: string; agent_name: string; turns_used: number; tools_called: string[]; metadata: Record<string, unknown> };

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
