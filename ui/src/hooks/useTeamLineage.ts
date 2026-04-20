import { api } from "../api/client";
import type { TeamLineage } from "../api/types";
import { useFetch } from "./useFetch";

export function useTeamLineage(name: string) {
  const { data, loading, error, refresh } = useFetch<TeamLineage>(
    () => api.getTeamLineage(name),
    [name],
  );
  return { lineage: data, loading, error, refresh };
}
