import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { AgentVersion, VersionStatus } from "../api/types";
import LoadingSpinner from "../components/LoadingSpinner";
import { useAgent } from "../hooks/useAgent";
import { useAgentVersions } from "../hooks/useAgentVersions";
import { cn } from "../lib/cn";

const STATUS_COLORS: Record<VersionStatus, string> = {
  deployed: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  draft: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300",
  proposed: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  archived: "bg-gray-100 text-gray-500 dark:bg-gray-900 dark:text-gray-500",
  rejected: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
};

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

export default function AgentVersions() {
  const { name = "" } = useParams<{ name: string }>();
  const { agent } = useAgent(name);
  const { versions, loading, error, refresh } = useAgentVersions(name);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [rejectId, setRejectId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const run = async (
    fn: () => Promise<unknown>,
    key: string,
  ): Promise<void> => {
    setBusy(key);
    setActionError(null);
    try {
      await fn();
      await refresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy(null);
    }
  };

  const deploy = (v: AgentVersion) =>
    run(() => api.deployAgentVersion(name, v.version_id), v.version_id);
  const propose = (v: AgentVersion) =>
    run(() => api.proposeAgentVersion(name, v.version_id), v.version_id);
  const approve = (v: AgentVersion) =>
    run(() => api.approveAgentVersion(name, v.version_id), v.version_id);
  const doReject = async (v: AgentVersion) => {
    if (!rejectReason.trim()) return;
    await run(
      () => api.rejectAgentVersion(name, v.version_id, rejectReason),
      v.version_id,
    );
    setRejectId(null);
    setRejectReason("");
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <header className="mb-4 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 mb-1">
            <Link to="/agents" className="hover:underline">
              Agents
            </Link>
            <span>/</span>
            <Link to={`/agents/${name}`} className="hover:underline">
              {name}
            </Link>
            <span>/</span>
            <span>Versions</span>
          </div>
          <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            Versions of {agent?.owner_id ?? "?"}:{name}
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to={`/agents/${name}/lineage`}
            className="text-xs px-3 py-1.5 rounded border border-gray-300 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-900/40"
          >
            View lineage
          </Link>
        </div>
      </header>

      {actionError && (
        <div className="mb-3 text-xs rounded border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/40 text-red-700 dark:text-red-300 px-3 py-2">
          {actionError}
        </div>
      )}

      {loading && <LoadingSpinner />}
      {error && (
        <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
      )}

      {!loading && versions.length === 0 && (
        <p className="text-sm text-gray-500">No versions yet.</p>
      )}

      <div className="space-y-1">
        {[...versions].reverse().map((v) => {
          const isBusy = busy === v.version_id;
          const statusClass =
            STATUS_COLORS[v.status] ??
            "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300";
          const rejecting = rejectId === v.version_id;
          return (
            <div
              key={v.version_id}
              className="rounded border border-gray-200 dark:border-gray-800 px-3 py-2 text-xs font-mono"
            >
              <div className="flex items-center gap-3 flex-wrap">
                <span className="font-semibold text-gray-900 dark:text-gray-100">
                  v{v.version_number}
                </span>
                <span
                  className={cn(
                    "inline-block rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
                    statusClass,
                  )}
                >
                  {v.status}
                </span>
                <span className="text-gray-500">by {v.created_by}</span>
                <span className="text-gray-400">{formatTime(v.created_at)}</span>
                {v.forked_from && (
                  <span className="text-gray-500">
                    forked from {v.forked_from.owner_id}:{v.forked_from.name}
                  </span>
                )}
                {v.commit_message && (
                  <span className="text-gray-700 dark:text-gray-300 italic truncate">
                    "{v.commit_message}"
                  </span>
                )}
                <span className="ml-auto flex items-center gap-2">
                  {v.status === "draft" && (
                    <>
                      <button
                        type="button"
                        onClick={() => propose(v)}
                        disabled={isBusy}
                        className="px-2 py-1 text-[11px] rounded border border-blue-300 dark:border-blue-700 text-blue-700 dark:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-950/40"
                      >
                        Propose
                      </button>
                      <button
                        type="button"
                        onClick={() => deploy(v)}
                        disabled={isBusy}
                        className="px-2 py-1 text-[11px] rounded border border-green-300 dark:border-green-700 text-green-700 dark:text-green-300 hover:bg-green-50 dark:hover:bg-green-950/40"
                      >
                        Deploy
                      </button>
                    </>
                  )}
                  {v.status === "proposed" && (
                    <>
                      <button
                        type="button"
                        onClick={() => approve(v)}
                        disabled={isBusy}
                        className="px-2 py-1 text-[11px] rounded border border-green-300 dark:border-green-700 text-green-700 dark:text-green-300 hover:bg-green-50 dark:hover:bg-green-950/40"
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          setRejectId(rejecting ? null : v.version_id)
                        }
                        disabled={isBusy}
                        className="px-2 py-1 text-[11px] rounded border border-red-300 dark:border-red-700 text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-950/40"
                      >
                        Reject
                      </button>
                    </>
                  )}
                  {(v.status === "proposed" || v.status === "archived") && (
                    <button
                      type="button"
                      onClick={() => deploy(v)}
                      disabled={isBusy}
                      className="px-2 py-1 text-[11px] rounded border border-gray-300 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-900/40"
                    >
                      Deploy
                    </button>
                  )}
                </span>
              </div>
              {rejecting && (
                <div className="mt-2 flex items-center gap-2">
                  <input
                    value={rejectReason}
                    onChange={(e) => setRejectReason(e.target.value)}
                    placeholder="Reason for rejection"
                    className="flex-1 text-[11px] rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1"
                  />
                  <button
                    type="button"
                    onClick={() => doReject(v)}
                    disabled={isBusy || !rejectReason.trim()}
                    className="px-2 py-1 text-[11px] rounded bg-red-600 text-white hover:bg-red-500 disabled:opacity-50"
                  >
                    Confirm reject
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
