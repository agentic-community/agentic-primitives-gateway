import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { AgentMemoryResponse, MemoryStoreInfo } from "../api/types";

interface MemoryPanelProps {
  agentName: string;
  sessionId: string;
  /** Incremented externally to trigger a refresh (e.g. after a chat turn). */
  refreshKey?: number;
}

export default function MemoryPanel({
  agentName,
  sessionId,
  refreshKey,
}: MemoryPanelProps) {
  const [data, setData] = useState<AgentMemoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [expandedStores, setExpandedStores] = useState<Set<string>>(new Set());

  const fetchMemory = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api.getAgentMemory(agentName, sessionId);
      setData(resp);
    } catch {
      // silently ignore — memory introspection is optional
    } finally {
      setLoading(false);
    }
  }, [agentName, sessionId]);

  useEffect(() => {
    fetchMemory();
  }, [fetchMemory, refreshKey]);

  const toggleStore = (ns: string) => {
    setExpandedStores((prev) => {
      const next = new Set(prev);
      if (next.has(ns)) next.delete(ns);
      else next.add(ns);
      return next;
    });
  };

  if (!data || !data.memory_enabled) return null;

  const totalMemories = data.stores.reduce(
    (sum, s) => sum + s.memory_count,
    0,
  );

  return (
    <div className="border border-gray-200 dark:border-gray-800 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-2 text-xs font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
      >
        <span className="flex items-center gap-2">
          <span className="text-[10px]">{expanded ? "▼" : "▶"}</span>
          Memory Stores
          {totalMemories > 0 && (
            <span className="inline-flex items-center rounded-full bg-blue-100 dark:bg-blue-900/40 px-1.5 py-0.5 text-[10px] font-medium text-blue-700 dark:text-blue-300">
              {totalMemories}
            </span>
          )}
        </span>
        <span className="text-[10px] font-mono text-gray-400 dark:text-gray-500">
          ns: {data.namespace}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-gray-200 dark:border-gray-800 px-3 py-2 space-y-2">
          {loading && (
            <p className="text-[11px] text-gray-400">Loading...</p>
          )}

          {data.stores.length === 0 && !loading && (
            <p className="text-[11px] text-gray-400 dark:text-gray-500">
              No memories stored yet. The agent will remember things as you
              chat.
            </p>
          )}

          {data.stores.map((store: MemoryStoreInfo) => (
            <div
              key={store.namespace}
              className="rounded border border-gray-100 dark:border-gray-800"
            >
              <button
                onClick={() => toggleStore(store.namespace)}
                className="w-full flex items-center justify-between px-2 py-1.5 text-[11px] hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
              >
                <span className="font-mono text-gray-700 dark:text-gray-300 flex items-center gap-1.5">
                  <span className="text-[9px]">
                    {expandedStores.has(store.namespace) ? "▼" : "▶"}
                  </span>
                  {store.namespace}
                </span>
                <span className="text-[10px] text-gray-400">
                  {store.memory_count}{" "}
                  {store.memory_count === 1 ? "memory" : "memories"}
                </span>
              </button>

              {expandedStores.has(store.namespace) && (
                <div className="border-t border-gray-100 dark:border-gray-800 px-2 py-1.5 space-y-1">
                  {store.memories.map((mem) => (
                    <div
                      key={mem.key}
                      className="flex flex-col gap-0.5 text-[11px] py-1 border-b border-gray-50 dark:border-gray-800/50 last:border-0"
                    >
                      <span className="font-mono font-medium text-gray-700 dark:text-gray-300">
                        {mem.key}
                      </span>
                      <span className="text-gray-500 dark:text-gray-400 break-words">
                        {mem.content}
                      </span>
                    </div>
                  ))}
                  {store.memories.length === 0 && (
                    <p className="text-[10px] text-gray-400">Empty</p>
                  )}
                </div>
              )}
            </div>
          ))}

          <button
            onClick={fetchMemory}
            disabled={loading}
            className="text-[10px] text-blue-600 dark:text-blue-400 hover:underline disabled:opacity-50"
          >
            Refresh
          </button>
        </div>
      )}
    </div>
  );
}
