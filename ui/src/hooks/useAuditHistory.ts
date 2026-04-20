import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AuditEvent, AuditFilters } from "../api/types";

interface UseAuditHistoryResult {
  events: AuditEvent[];
  /** True while the first page (or a filter change) is loading. */
  loading: boolean;
  /** True while ``loadOlder()`` is in flight. */
  loadingMore: boolean;
  /** Error message from the last failed request, or null. */
  error: string | null;
  /** True when no more pages are available for the current filter set. */
  exhausted: boolean;
  /** Fetch the next page of older events. */
  loadOlder: () => Promise<void>;
  /** Re-fetch from the newest entry. */
  refresh: () => Promise<void>;
}

const PAGE_SIZE = 100;

/**
 * Paginated historical browse of the audit Redis stream.
 *
 * Uses ``GET /api/v1/audit/events`` with XREVRANGE semantics — events
 * arrive newest-first.  ``loadOlder()`` advances the cursor backward by
 * passing the prior response's ``next`` as the new ``end``.
 *
 * Refetches from scratch whenever ``filters`` changes.
 */
export function useAuditHistory(filters: AuditFilters): UseAuditHistoryResult {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [exhausted, setExhausted] = useState(false);

  // Re-run the initial fetch when filters change.  Filters are a plain
  // object; stringify to compare across renders without deep-equal.
  const filterKey = JSON.stringify(filters);

  const fetchNewest = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.listAuditEvents(filters, "-", "+", PAGE_SIZE);
      setEvents(resp.events);
      setNextCursor(resp.next);
      setExhausted(resp.next === null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setEvents([]);
      setExhausted(true);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey]);

  useEffect(() => {
    fetchNewest();
  }, [fetchNewest]);

  const loadOlder = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const resp = await api.listAuditEvents(filters, "-", nextCursor, PAGE_SIZE);
      setEvents((prev) => [...prev, ...resp.events]);
      setNextCursor(resp.next);
      if (resp.next === null) setExhausted(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingMore(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey, nextCursor, loadingMore]);

  const refresh = useCallback(async () => {
    await fetchNewest();
  }, [fetchNewest]);

  // Keep the ref in sync so tests can observe without re-rendering.
  const eventsRef = useRef(events);
  eventsRef.current = events;

  return { events, loading, loadingMore, error, exhausted, loadOlder, refresh };
}
