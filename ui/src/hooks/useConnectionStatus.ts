import { useCallback, useEffect, useRef, useState } from "react";

export type ConnectionState = "connected" | "disconnected" | "connecting";

/**
 * Polls the server health endpoint to track connection state.
 * Returns the current connection status and updates live.
 */
export function useConnectionStatus(intervalMs = 5000): ConnectionState {
  const [state, setState] = useState<ConnectionState>("connecting");
  const failCountRef = useRef(0);

  const check = useCallback(async () => {
    try {
      const res = await fetch("/healthz", { signal: AbortSignal.timeout(3000) });
      if (res.ok) {
        failCountRef.current = 0;
        setState("connected");
      } else {
        failCountRef.current++;
        setState(failCountRef.current >= 2 ? "disconnected" : "connected");
      }
    } catch {
      failCountRef.current++;
      // Show disconnected after 2 consecutive failures to avoid flicker
      setState(failCountRef.current >= 2 ? "disconnected" : "connected");
    }
  }, []);

  useEffect(() => {
    check();
    const id = setInterval(check, intervalMs);
    return () => clearInterval(id);
  }, [check, intervalMs]);

  return state;
}
