import { api } from "../api/client";
import type { AgentSpec } from "../api/types";
import { useFetch } from "./useFetch";

export function useAgents() {
  const { data, loading, error, refresh } = useFetch<AgentSpec[]>(
    () => api.listAgents(),
  );
  return { agents: data ?? [], loading, error, refresh };
}
