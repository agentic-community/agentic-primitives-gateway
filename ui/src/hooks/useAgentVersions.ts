import { api } from "../api/client";
import type { AgentVersion } from "../api/types";
import { useFetch } from "./useFetch";

export function useAgentVersions(name: string) {
  const { data, loading, error, refresh } = useFetch<AgentVersion[]>(
    () => api.listAgentVersions(name),
    [name],
  );
  return { versions: data ?? [], loading, error, refresh };
}
