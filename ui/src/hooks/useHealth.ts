import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { HealthResponse, ReadinessResponse } from "../api/types";

export function useHealth() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, r] = await Promise.all([api.health(), api.readiness()]);
      setHealth(h);
      setReadiness(r);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch health");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { health, readiness, loading, error, refresh };
}
