import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { AgentSpec } from "../api/types";

export function useAgent(name: string) {
  const [agent, setAgent] = useState<AgentSpec | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setAgent(await api.getAgent(name));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch agent");
    } finally {
      setLoading(false);
    }
  }, [name]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { agent, loading, error, refresh };
}
