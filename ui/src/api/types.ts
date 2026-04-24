export interface PrimitiveConfig {
  enabled: boolean;
  tools: string[] | null;
  namespace: string | null;
  shared_namespaces: string[] | null;
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
  | { type: "done"; response: string; session_id: string; agent_name: string; turns_used: number; tools_called: string[]; artifacts?: StreamArtifact[]; metadata: Record<string, unknown> }
  | { type: "error"; detail: string };

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
  shared_memory_namespace?: string;
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
  shared_memory_namespace?: string | null;
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
  | { type: "cancelled" }
  | { type: "task_retry"; agent: string; task_id: string; title: string }
  | { type: "retry_done"; task_id: string }
  | { type: "error"; detail: string };

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

export interface CredentialStatusResponse {
  source: string;
  aws_configured: boolean;
  aws_credential_expiry: string | null;
  server_credentials: string; // "never" | "fallback" | "always"
  required_credentials: string[]; // e.g. ["aws", "langfuse"]
}

// ── Auth / Audit ─────────────────────────────────────────────────────

export interface WhoAmIResponse {
  id: string;
  type: string;
  is_admin: boolean;
  groups: string[];
  scopes: string[];
}

// Runtime-iterable lists for the two audit enums.  Deriving the TS union
// type from these arrays (via ``typeof ... [number]``) keeps the type and
// the UI dropdowns in lockstep — add a value here and the compiler
// enforces exhaustive handling everywhere it's used.  Mirror the server
// ``AuditOutcome`` / ``ResourceType`` StrEnums in ``audit/models.py``.
// Listed alphabetically so the UI dropdown order is predictable.
export const AUDIT_OUTCOMES = [
  "allow",
  "deny",
  "error",
  "failure",
  "not_implemented",
  "success",
] as const;
export type AuditOutcome = (typeof AUDIT_OUTCOMES)[number];

export const AUDIT_RESOURCE_TYPES = [
  "agent",
  "code_execution",
  "config",
  "credential",
  "evaluator",
  "file",
  "http",
  "identity",
  "llm",
  "memory",
  "page",
  "policy",
  "policy_engine",
  "session",
  "task",
  "team",
  "tool",
  "trace",
  "user",
] as const;
export type AuditResourceType = (typeof AUDIT_RESOURCE_TYPES)[number];

export interface AuditEvent {
  schema_version: "1";
  event_id: string;
  timestamp: string;
  action: string;
  outcome: AuditOutcome;
  actor_id: string | null;
  actor_type: string | null;
  actor_groups: string[];
  resource_type: AuditResourceType | null;
  resource_id: string | null;
  request_id: string | null;
  correlation_id: string | null;
  source_ip: string | null;
  user_agent: string | null;
  http_method: string | null;
  http_path: string | null;
  http_status: number | null;
  duration_ms: number | null;
  reason: string | null;
  metadata: Record<string, unknown>;
}

export interface AuditStatus {
  stream_sink_configured: boolean;
  stream_name: string | null;
  length: number | null;
  maxlen: number | null;
}

export interface AuditListResponse {
  events: AuditEvent[];
  next: string | null;
  scanned: number;
}

export interface AuditFilters {
  action?: string;
  action_category?: string;
  outcome?: AuditOutcome[];
  actor_id?: string;
  resource_type?: AuditResourceType[];
  resource_id?: string;
  correlation_id?: string;
}

// ── Versioning / Fork / Lineage ─────────────────────────────────────────

export type VersionStatus = "draft" | "proposed" | "deployed" | "archived" | "rejected";

export interface ForkRef {
  name: string;
  owner_id: string;
  version_id: string;
}

export interface Identity {
  owner_id: string;
  name: string;
}

export interface AgentVersion {
  version_id: string;
  agent_name: string;
  owner_id: string;
  version_number: number;
  spec: AgentSpec;
  created_at: string;
  created_by: string;
  parent_version_id: string | null;
  forked_from: ForkRef | null;
  status: VersionStatus;
  approved_by: string | null;
  approved_at: string | null;
  deployed_at: string | null;
  commit_message: string | null;
}

export interface TeamVersion {
  version_id: string;
  team_name: string;
  owner_id: string;
  version_number: number;
  spec: TeamSpec;
  created_at: string;
  created_by: string;
  parent_version_id: string | null;
  forked_from: ForkRef | null;
  status: VersionStatus;
  approved_by: string | null;
  approved_at: string | null;
  deployed_at: string | null;
  commit_message: string | null;
}

export interface AgentVersionListResponse {
  versions: AgentVersion[];
}

export interface TeamVersionListResponse {
  versions: TeamVersion[];
}

export interface AgentLineageNode {
  version: AgentVersion;
  children_ids: string[];
  forks_out: ForkRef[];
}

export interface AgentLineage {
  root_identity: Identity;
  nodes: AgentLineageNode[];
  deployed: Record<string, string>;
}

export interface TeamLineageNode {
  version: TeamVersion;
  children_ids: string[];
  forks_out: ForkRef[];
}

export interface TeamLineage {
  root_identity: Identity;
  nodes: TeamLineageNode[];
  deployed: Record<string, string>;
}

export interface CreateVersionRequest {
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
  commit_message?: string;
  parent_version_id?: string;
}

export interface CreateTeamVersionRequest {
  description?: string;
  planner?: string;
  synthesizer?: string;
  workers?: string[];
  max_concurrent?: number | null;
  global_max_turns?: number;
  global_timeout_seconds?: number;
  shared_memory_namespace?: string | null;
  shared_with?: string[];
  checkpointing_enabled?: boolean;
  commit_message?: string;
  parent_version_id?: string;
}

export interface ForkRequest {
  target_name?: string;
  commit_message?: string;
}

export interface RejectionRequest {
  reason: string;
}
