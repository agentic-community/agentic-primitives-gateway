import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { AuditEvent } from "../api/types";
import { parseSSE } from "../lib/sse";

export type AuditStreamStatus =
  | "idle"
  | "connecting"
  | "open"
  | "error"
  | "paused";

interface UseAuditStreamOptions {
  /** Stop consuming new events when true.  Re-enabling resumes at "now" (drops backlog). */
  paused?: boolean;
  /** Max events retained in memory.  Older events drop on overflow. */
  bufferSize?: number;
}

interface UseAuditStreamResult {
  events: AuditEvent[];
  status: AuditStreamStatus;
  /** Events dropped due to ring-buffer overflow — NOT server-side drops. */
  dropped: number;
  clear: () => void;
  error: string | null;
}

const DEFAULT_BUFFER = 1000;

/**
 * SSE live tail of the audit Redis stream.
 *
 * Connects to ``GET /api/v1/audit/events/stream`` (XREAD ``$`` under the
 * hood) and keeps the most recent N events in memory, newest-first.
 * The tail is always broad (no server-side filter) — consumers filter
 * client-side so adjusting filters is instant and doesn't drop events
 * from the buffer.  Only ``paused`` changes restart the connection.
 *
 * Keepalive comment frames (``: keepalive``) from the server are ignored
 * by ``parseSSE`` since they have no ``data:`` prefix.
 */
export function useAuditStream(
  options: UseAuditStreamOptions = {},
): UseAuditStreamResult {
  const { paused = false, bufferSize = DEFAULT_BUFFER } = options;

  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [status, setStatus] = useState<AuditStreamStatus>("idle");
  const [dropped, setDropped] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const clear = useCallback(() => {
    setEvents([]);
    setDropped(0);
  }, []);

  // Keep the current pause state available to the running loop without
  // restarting the connection on every toggle render.
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  useEffect(() => {
    if (paused) {
      setStatus("paused");
      return;
    }

    const controller = new AbortController();
    let cancelled = false;
    setStatus("connecting");
    setError(null);

    (async () => {
      try {
        const stream = api.streamAuditEvents(controller.signal);
        const reader = stream.getReader();
        let buffer = "";
        setStatus("open");
        while (!cancelled) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += value;
          // SSE frames are delimited by a blank line.  Split on "\n\n"
          // and parse fully-received frames.
          const lastBoundary = buffer.lastIndexOf("\n\n");
          if (lastBoundary === -1) continue;
          const complete = buffer.slice(0, lastBoundary + 2);
          buffer = buffer.slice(lastBoundary + 2);

          const parsed = parseSSE<AuditEvent>(complete);
          if (parsed.length === 0) continue;
          if (pausedRef.current) continue;

          setEvents((prev) => {
            // Newest-first ordering.  Prepend then trim.
            const next = [...parsed.reverse(), ...prev];
            if (next.length > bufferSize) {
              setDropped((d) => d + (next.length - bufferSize));
              return next.slice(0, bufferSize);
            }
            return next;
          });
        }
      } catch (e) {
        if (cancelled || controller.signal.aborted) return;
        setError(e instanceof Error ? e.message : String(e));
        setStatus("error");
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [paused, bufferSize]);

  return { events, status, dropped, clear, error };
}
