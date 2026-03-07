import { api } from "../api/client";
import type { HealthResponse, ReadinessResponse } from "../api/types";
import { useFetch } from "./useFetch";

export function useHealth() {
  const { data, loading, error, refresh } = useFetch<{
    health: HealthResponse;
    readiness: ReadinessResponse;
  }>(async () => {
    const [health, readiness] = await Promise.all([
      api.health(),
      api.readiness(),
    ]);
    return { health, readiness };
  });
  return {
    health: data?.health ?? null,
    readiness: data?.readiness ?? null,
    loading,
    error,
    refresh,
  };
}
