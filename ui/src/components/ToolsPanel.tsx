import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { AgentToolInfo, AgentToolsResponse } from "../api/types";

interface ToolsPanelProps {
  agentName: string;
}

const PRIMITIVE_COLORS: Record<string, string> = {
  memory:
    "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300",
  browser:
    "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
  code_interpreter:
    "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300",
  tools:
    "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300",
  identity:
    "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300",
};

function groupByPrimitive(
  tools: AgentToolInfo[],
): Record<string, AgentToolInfo[]> {
  const groups: Record<string, AgentToolInfo[]> = {};
  for (const tool of tools) {
    if (!groups[tool.primitive]) groups[tool.primitive] = [];
    groups[tool.primitive].push(tool);
  }
  return groups;
}

export default function ToolsPanel({ agentName }: ToolsPanelProps) {
  const [data, setData] = useState<AgentToolsResponse | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    api.getAgentTools(agentName).then(setData).catch(() => {});
  }, [agentName]);

  if (!data || data.tools.length === 0) return null;

  const grouped = groupByPrimitive(data.tools);

  return (
    <div className="border border-gray-200 dark:border-gray-800 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-2 text-xs font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
      >
        <span className="flex items-center gap-2">
          <span className="text-[10px]">{expanded ? "▼" : "▶"}</span>
          Tools
          <span className="inline-flex items-center rounded-full bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 text-[10px] font-medium text-gray-600 dark:text-gray-300">
            {data.tools.length}
          </span>
        </span>
        <span className="text-[10px] text-gray-400 dark:text-gray-500">
          {Object.keys(grouped).length} primitive
          {Object.keys(grouped).length !== 1 ? "s" : ""}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-gray-200 dark:border-gray-800 px-3 py-2 space-y-2">
          {Object.entries(grouped).map(([primitive, tools]) => {
            const provider = tools[0]?.provider ?? "unknown";
            const colorClass =
              PRIMITIVE_COLORS[primitive] ??
              "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300";
            return (
              <div key={primitive} className="space-y-1">
                <div className="flex items-center gap-2">
                  <span
                    className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-bold ${colorClass}`}
                  >
                    {primitive}
                  </span>
                  <span className="text-[10px] font-mono text-gray-400 dark:text-gray-500">
                    provider: {provider}
                  </span>
                </div>
                <div className="grid gap-1 pl-2">
                  {tools.map((tool) => (
                    <div
                      key={tool.name}
                      className="flex items-start gap-2 text-[11px] py-0.5"
                    >
                      <span className="font-mono font-medium text-gray-700 dark:text-gray-300 shrink-0">
                        {tool.name}
                      </span>
                      <span className="text-gray-400 dark:text-gray-500">
                        {tool.description}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
