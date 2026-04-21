import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { TeamVersion } from "../api/types";
import LineageGraph from "../components/LineageGraph";
import LoadingSpinner from "../components/LoadingSpinner";
import { useTeamLineage } from "../hooks/useTeamLineage";

export default function TeamLineage() {
  const { name = "" } = useParams<{ name: string }>();
  const { lineage, loading, error, refresh } = useTeamLineage(name);
  const [selected, setSelected] = useState<TeamVersion | null>(null);
  const [deploying, setDeploying] = useState(false);

  const handleDeploy = async (v: TeamVersion) => {
    setDeploying(true);
    try {
      await api.deployTeamVersion(`${v.owner_id}:${v.team_name}`, v.version_id);
      await refresh();
    } finally {
      setDeploying(false);
    }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <header className="mb-4">
        <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 mb-1">
          <Link to="/teams" className="hover:underline">
            Teams
          </Link>
          <span>/</span>
          <span>{name}</span>
          <span>/</span>
          <span>Lineage</span>
        </div>
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Lineage of {name}
        </h1>
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Solid edges: version history within an identity.  Dashed edges:
          forks into another owner's namespace.
        </p>
      </header>

      {loading && <LoadingSpinner />}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}

      {lineage && lineage.nodes.length > 0 && (
        <div className="flex gap-4">
          <div className="flex-1">
            <LineageGraph
              nodes={lineage.nodes.map((n) => ({ kind: "team", node: n }))}
              deployed={lineage.deployed}
              onSelect={(v) => setSelected(v as TeamVersion)}
              selectedVersionId={selected?.version_id ?? null}
            />
          </div>
          {selected && (
            <aside className="w-80 shrink-0 rounded border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 p-3 text-xs font-mono max-h-[60vh] overflow-auto">
              <div className="flex items-center justify-between mb-2">
                <span className="font-semibold text-gray-900 dark:text-gray-100">
                  v{selected.version_number}
                </span>
                <button
                  type="button"
                  onClick={() => setSelected(null)}
                  className="text-[10px] text-gray-500 hover:text-gray-900 dark:hover:text-gray-100"
                >
                  Close
                </button>
              </div>
              <div className="text-gray-500 dark:text-gray-400 mb-2">
                {selected.owner_id}:{selected.team_name}
              </div>
              {selected.status !== "deployed" && (
                <button
                  type="button"
                  onClick={() => handleDeploy(selected)}
                  disabled={deploying}
                  className="mb-2 w-full px-2 py-1 text-[11px] rounded border border-green-300 dark:border-green-700 text-green-700 dark:text-green-300 hover:bg-green-50 dark:hover:bg-green-950/40 disabled:opacity-50"
                >
                  Deploy this version
                </button>
              )}
              <pre className="text-[10px] bg-gray-50 dark:bg-gray-950 rounded p-2 overflow-x-auto whitespace-pre-wrap">
                {JSON.stringify(selected, null, 2)}
              </pre>
            </aside>
          )}
        </div>
      )}

      {lineage && lineage.nodes.length === 0 && (
        <p className="text-sm text-gray-500">No versions in this lineage.</p>
      )}
    </div>
  );
}
