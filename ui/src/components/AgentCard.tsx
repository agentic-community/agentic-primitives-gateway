import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AgentSpec } from "../api/types";

export default function AgentCard({ agent }: { agent: AgentSpec }) {
  const primitiveNames = Object.keys(agent.primitives).filter(
    (k) => agent.primitives[k].enabled,
  );

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-800 p-4 flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold font-mono text-gray-900 dark:text-gray-100">
            {agent.name}
          </h3>
          {agent.description && (
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              {agent.description}
            </p>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={() => api.exportAgent(agent.name)}
            className="rounded border border-gray-300 dark:border-gray-700 px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
            title="Export as Python script"
          >
            Export
          </button>
          <Link
            to={`/agents/${agent.name}/chat`}
            className="rounded bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-indigo-700"
          >
            Chat
          </Link>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="font-mono text-gray-600 dark:text-gray-400">
          {agent.model}
        </span>
        {primitiveNames.length > 0 && (
          <span className="text-gray-400 dark:text-gray-600">|</span>
        )}
        {primitiveNames.map((p) => (
          <span
            key={p}
            className="rounded bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 font-mono text-gray-600 dark:text-gray-400"
          >
            {p}
          </span>
        ))}
      </div>
    </div>
  );
}
