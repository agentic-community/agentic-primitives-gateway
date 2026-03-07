import { api } from "../api/client";
import type { TeamSpec } from "../api/types";
import { useFetch } from "./useFetch";

export function useTeams() {
  const { data, loading, error, refresh } = useFetch<TeamSpec[]>(
    () => api.listTeams(),
  );
  return { teams: data ?? [], loading, error, refresh };
}
