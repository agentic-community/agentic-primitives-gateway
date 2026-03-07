import { api } from "../api/client";
import type { ProvidersResponse } from "../api/types";
import { useFetch } from "./useFetch";

export function useProviders() {
  const { data, loading, error, refresh } = useFetch<ProvidersResponse>(
    () => api.providers(),
  );
  return { providers: data, loading, error, refresh };
}
