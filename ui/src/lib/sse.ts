import type { StreamEvent } from "../api/types";

/** Parse SSE `data:` lines from a chunk into typed events. */
export function parseSSE(chunk: string): StreamEvent[] {
  const events: StreamEvent[] = [];
  const lines = chunk.split("\n");
  for (const line of lines) {
    if (line.startsWith("data: ")) {
      try {
        events.push(JSON.parse(line.slice(6)));
      } catch {
        // skip malformed lines
      }
    }
  }
  return events;
}
