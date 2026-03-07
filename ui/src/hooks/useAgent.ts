import { api } from "../api/client";
import type { AgentSpec } from "../api/types";
import { useFetch } from "./useFetch";

export function useAgent(name: string) {
  const { data, loading, error, refresh } = useFetch<AgentSpec>(
    () => api.getAgent(name),
    [name],
  );
  return { agent: data, loading, error, refresh };
}
