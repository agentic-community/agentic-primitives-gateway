import { api } from "../api/client";
import type { AgentVersion, TeamVersion } from "../api/types";
import { useFetch } from "./useFetch";

export function useAgentProposals() {
  const { data, loading, error, refresh } = useFetch<AgentVersion[]>(
    () => api.listPendingAgentProposals(),
    [],
  );
  return { proposals: data ?? [], loading, error, refresh };
}

export function useTeamProposals() {
  const { data, loading, error, refresh } = useFetch<TeamVersion[]>(
    () => api.listPendingTeamProposals(),
    [],
  );
  return { proposals: data ?? [], loading, error, refresh };
}
