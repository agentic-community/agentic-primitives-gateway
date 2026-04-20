import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { AgentSpec } from "../api/types";

export default function AgentCard({ agent }: { agent: AgentSpec }) {
  const navigate = useNavigate();
  const [forkBusy, setForkBusy] = useState(false);

  const primitiveNames = Object.keys(agent.primitives).filter(
    (k) => agent.primitives[k].enabled,
  );

  const qualified = `${agent.owner_id}:${agent.name}`;

  const handleFork = async () => {
    setForkBusy(true);
    try {
      const version = await api.forkAgent(qualified, { target_name: agent.name });
      navigate(`/agents/${version.owner_id}:${version.agent_name}/versions`);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Fork failed");
    } finally {
      setForkBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-800 p-4 flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold font-mono text-gray-900 dark:text-gray-100">
              {agent.name}
            </h3>
            <span
              className="text-[10px] font-mono text-gray-500 dark:text-gray-400"
              title={`owner: ${agent.owner_id}`}
            >
              @{agent.owner_id}
            </span>
          </div>
          {agent.description && (
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              {agent.description}
            </p>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <Link
            to={`/agents/${qualified}/versions`}
            className="rounded border border-gray-300 dark:border-gray-700 px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
            title="Version history"
          >
            Versions
          </Link>
          <Link
            to={`/agents/${qualified}/lineage`}
            className="rounded border border-gray-300 dark:border-gray-700 px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
            title="Lineage graph"
          >
            Lineage
          </Link>
          <button
            type="button"
            onClick={handleFork}
            disabled={forkBusy}
            className="rounded border border-gray-300 dark:border-gray-700 px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
            title="Fork into my namespace"
          >
            Fork
          </button>
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
