import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Generic data fetching hook with loading/error state and refresh.
 *
 * Replaces repeated fetch boilerplate across useAgent, useAgents, useHealth, useProviders.
 */
export function useFetch<T>(
  fetchFn: () => Promise<T>,
  deps: unknown[] = [],
) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const hasFetched = useRef(false);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const refresh = useCallback(async () => {
    // Only show loading spinner on initial fetch.
    // Subsequent refreshes update data in place without flashing a spinner.
    if (!hasFetched.current) setLoading(true);
    setError(null);
    try {
      setData(await fetchFn());
      hasFetched.current = true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch");
    } finally {
      setLoading(false);
    }
  }, deps);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { data, loading, error, refresh };
}
