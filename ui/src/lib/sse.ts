/** Parse SSE `data:` lines from a chunk into typed events. */
export function parseSSE<T = Record<string, unknown>>(chunk: string): T[] {
  const events: T[] = [];
  for (const line of chunk.split("\n")) {
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
