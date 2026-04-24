import { api } from "../api/client";
import type { HealthResponse } from "../api/types";
import { useFetch } from "./useFetch";

/**
 * Liveness-only health hook. `/healthz` is an auth-exempt liveness probe
 * that returns `{status: "ok"}` as long as the FastAPI process is alive.
 * Provider-level readiness used to live in this hook via `/readyz`, but
 * that endpoint is anonymous (kubelet contract) — the dashboard now reads
 * `/api/v1/providers/status` directly so healthcheck audit events are
 * attributed to the logged-in user. See `Dashboard.tsx`.
 */
export function useHealth() {
  const { data, loading, error, refresh } = useFetch<HealthResponse>(() =>
    api.health(),
  );
  return { health: data, loading, error, refresh };
}
