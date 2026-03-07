import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { AgentSpec, CatalogToolInfo, PrimitiveConfig } from "../api/types";

interface PrimitivesSelectorProps {
  value: Record<string, PrimitiveConfig>;
  onChange: (value: Record<string, PrimitiveConfig>) => void;
  /** Agent name to exclude from the agents sub-agent list (prevents self-delegation). */
  excludeAgent?: string;
}

export default function PrimitivesSelector({
  value,
  onChange,
  excludeAgent,
}: PrimitivesSelectorProps) {
  const [catalog, setCatalog] = useState<Record<string, CatalogToolInfo[]>>({});
  const [agents, setAgents] = useState<AgentSpec[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    Promise.all([
      api.getToolCatalog().then((r) => setCatalog(r.primitives)),
      api.listAgents().then(setAgents),
    ])
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const availableAgents = agents.filter((a) => a.name !== excludeAgent);

  const togglePrimitive = (name: string) => {
    const next = { ...value };
    if (next[name]?.enabled) {
      delete next[name];
    } else {
      next[name] = { enabled: true, tools: null, namespace: null };
    }
    onChange(next);
  };

  const toggleTool = (primitive: string, toolName: string) => {
    const next = { ...value };
    const config = next[primitive];
    if (!config) return;

    const allTools = catalog[primitive]?.map((t) => t.name) ?? [];

    if (config.tools === null) {
      const filtered = allTools.filter((t) => t !== toolName);
      next[primitive] = { ...config, tools: filtered };
    } else {
      const has = config.tools.includes(toolName);
      const updated = has
        ? config.tools.filter((t) => t !== toolName)
        : [...config.tools, toolName];
      next[primitive] = {
        ...config,
        tools: updated.length === allTools.length ? null : updated,
      };
    }
    onChange(next);
  };

  const toggleAgent = (agentName: string) => {
    const next = { ...value };
    const config = next["agents"];
    if (!config) return;

    const current = config.tools ?? [];
    const has = current.includes(agentName);
    const updated = has
      ? current.filter((t) => t !== agentName)
      : [...current, agentName];
    next["agents"] = { ...config, tools: updated.length > 0 ? updated : null };
    onChange(next);
  };

  const isToolEnabled = (primitive: string, toolName: string): boolean => {
    const config = value[primitive];
    if (!config?.enabled) return false;
    if (config.tools === null) return true;
    return config.tools.includes(toolName);
  };

  const isAgentEnabled = (agentName: string): boolean => {
    const config = value["agents"];
    if (!config?.enabled) return false;
    if (config.tools === null) return false; // agents require explicit selection
    return config.tools.includes(agentName);
  };

  const toggleExpanded = (name: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  if (loading) {
    return (
      <div className="text-xs text-gray-400 py-2">
        Loading primitives...
      </div>
    );
  }

  const primitiveNames = Object.keys(catalog);

  if (primitiveNames.length === 0) {
    return (
      <div className="text-xs text-gray-400 py-2">
        No primitives available.
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
        Primitives & Tools
      </label>
      <div className="rounded border border-gray-300 dark:border-gray-700 divide-y divide-gray-200 dark:divide-gray-700">
        {primitiveNames.map((name) => {
          const enabled = value[name]?.enabled ?? false;
          const isAgentsPrimitive = name === "agents";
          const tools = catalog[name];
          const hasExpandable = isAgentsPrimitive
            ? availableAgents.length > 0
            : tools.length > 0;
          const expandLabel = isAgentsPrimitive
            ? `${availableAgents.length} agents`
            : `${tools.length} tools`;
          const isOpen = expanded.has(name);

          return (
            <div key={name}>
              <div className="flex items-center gap-2 px-3 py-2">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={() => togglePrimitive(name)}
                  className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500 h-3.5 w-3.5"
                />
                <span
                  className={`text-sm font-mono font-medium flex-1 ${
                    enabled
                      ? "text-gray-900 dark:text-gray-100"
                      : "text-gray-400 dark:text-gray-500"
                  }`}
                >
                  {name}
                </span>
                {hasExpandable && enabled && (
                  <button
                    type="button"
                    onClick={() => toggleExpanded(name)}
                    className="text-[10px] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 px-1"
                  >
                    {isOpen ? (isAgentsPrimitive ? "hide agents" : "hide tools") : expandLabel}
                  </button>
                )}
                {isAgentsPrimitive && enabled && availableAgents.length === 0 && (
                  <span className="text-[10px] text-gray-400">no other agents</span>
                )}
              </div>

              {/* Tool checkboxes for regular primitives */}
              {!isAgentsPrimitive && tools.length > 0 && enabled && isOpen && (
                <div className="px-3 pb-2 pl-8 space-y-1">
                  {tools.map((tool) => (
                    <label
                      key={tool.name}
                      className="flex items-start gap-2 cursor-pointer group"
                    >
                      <input
                        type="checkbox"
                        checked={isToolEnabled(name, tool.name)}
                        onChange={() => toggleTool(name, tool.name)}
                        className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500 h-3.5 w-3.5 mt-0.5"
                      />
                      <div className="min-w-0">
                        <span className="text-xs font-mono text-gray-700 dark:text-gray-300">
                          {tool.name}
                        </span>
                        <p className="text-[11px] text-gray-400 dark:text-gray-500 leading-tight">
                          {tool.description}
                        </p>
                      </div>
                    </label>
                  ))}
                </div>
              )}

              {/* Agent checkboxes for the agents primitive */}
              {isAgentsPrimitive && enabled && isOpen && availableAgents.length > 0 && (
                <div className="px-3 pb-2 pl-8 space-y-1">
                  <p className="text-[11px] text-gray-400 dark:text-gray-500 mb-1">
                    Select agents this agent can delegate to:
                  </p>
                  {availableAgents.map((agent) => (
                    <label
                      key={agent.name}
                      className="flex items-start gap-2 cursor-pointer group"
                    >
                      <input
                        type="checkbox"
                        checked={isAgentEnabled(agent.name)}
                        onChange={() => toggleAgent(agent.name)}
                        className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 focus:ring-indigo-500 h-3.5 w-3.5 mt-0.5"
                      />
                      <div className="min-w-0">
                        <span className="text-xs font-mono text-gray-700 dark:text-gray-300">
                          {agent.name}
                        </span>
                        {agent.description && (
                          <p className="text-[11px] text-gray-400 dark:text-gray-500 leading-tight">
                            {agent.description}
                          </p>
                        )}
                        <p className="text-[10px] text-gray-400 dark:text-gray-600 font-mono">
                          {agent.model}
                        </p>
                      </div>
                    </label>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
