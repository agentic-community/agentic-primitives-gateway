import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ProvidersResponse } from "../api/types";

export function useProviders() {
  const [providers, setProviders] = useState<ProvidersResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setProviders(await api.providers());
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to fetch providers",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { providers, loading, error, refresh };
}
