import { api } from "../api/client";
import type { TeamVersion } from "../api/types";
import { useFetch } from "./useFetch";

export function useTeamVersions(name: string) {
  const { data, loading, error, refresh } = useFetch<TeamVersion[]>(
    () => api.listTeamVersions(name),
    [name],
  );
  return { versions: data ?? [], loading, error, refresh };
}
