import { api } from "../api/client";
import type { AgentLineage } from "../api/types";
import { useFetch } from "./useFetch";

export function useAgentLineage(name: string) {
  const { data, loading, error, refresh } = useFetch<AgentLineage>(
    () => api.getAgentLineage(name),
    [name],
  );
  return { lineage: data, loading, error, refresh };
}
