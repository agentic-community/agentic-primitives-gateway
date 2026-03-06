import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { PolicyEngineInfo, PolicyInfo } from "../api/types";
import LoadingSpinner from "../components/LoadingSpinner";

function CreatePolicyForm({
  engineId,
  onCreated,
}: {
  engineId: string;
  onCreated: () => void;
}) {
  const [body, setBody] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!body.trim()) return;
      setSubmitting(true);
      setError(null);
      try {
        await api.createPolicy(engineId, body.trim(), description.trim());
        setBody("");
        setDescription("");
        onCreated();
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to create policy",
        );
      } finally {
        setSubmitting(false);
      }
    },
    [engineId, body, description, onCreated],
  );

  return (
    <form onSubmit={handleSubmit} className="space-y-2">
      {error && (
        <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
      )}
      <input
        placeholder="Description (optional)"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm"
      />
      <textarea
        required
        placeholder='Cedar policy, e.g. permit(principal, action == Action::"agents:list_agents", resource);'
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={3}
        className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono"
      />
      <button
        type="submit"
        disabled={submitting || !body.trim()}
        className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
      >
        {submitting ? "Adding..." : "Add Policy"}
      </button>
    </form>
  );
}

function EngineSection({
  engine,
  onRefresh,
}: {
  engine: PolicyEngineInfo;
  onRefresh: () => void;
}) {
  const [policies, setPolicies] = useState<PolicyInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);

  const loadPolicies = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await api.listPolicies(engine.policy_engine_id);
      setPolicies(resp.policies);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [engine.policy_engine_id]);

  useEffect(() => {
    loadPolicies();
  }, [loadPolicies]);

  const handleDeletePolicy = useCallback(
    async (policyId: string) => {
      setDeleting(policyId);
      try {
        await api.deletePolicy(engine.policy_engine_id, policyId);
        loadPolicies();
      } catch {
        // ignore
      } finally {
        setDeleting(null);
      }
    },
    [engine.policy_engine_id, loadPolicies],
  );

  const handleDeleteEngine = useCallback(async () => {
    if (!confirm(`Delete engine "${engine.name}"? All its policies will be removed.`))
      return;
    await api.deletePolicyEngine(engine.policy_engine_id);
    onRefresh();
  }, [engine, onRefresh]);

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-800">
      <div className="flex items-center justify-between px-4 py-3 bg-gray-50 dark:bg-gray-900 rounded-t-lg">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-gray-100"
        >
          <span
            className={`text-[10px] transition-transform ${expanded ? "rotate-90" : ""}`}
          >
            &#9654;
          </span>
          <span className="font-mono">{engine.name}</span>
          <span className="text-xs font-normal text-gray-500 dark:text-gray-400">
            ({policies.length} {policies.length === 1 ? "policy" : "policies"})
          </span>
        </button>
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium ${
              engine.status === "ACTIVE"
                ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300"
                : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400"
            }`}
          >
            {engine.status}
          </span>
          <button
            onClick={handleDeleteEngine}
            className="rounded border border-red-300 dark:border-red-800 px-2 py-0.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950"
          >
            Delete
          </button>
        </div>
      </div>

      {expanded && (
        <div className="p-4 space-y-3">
          {engine.description && (
            <p className="text-xs text-gray-500 dark:text-gray-400">
              {engine.description}
            </p>
          )}

          {loading ? (
            <LoadingSpinner />
          ) : policies.length === 0 ? (
            <p className="text-xs text-gray-500 dark:text-gray-400 italic">
              No policies. Add one below.
            </p>
          ) : (
            <div className="space-y-2">
              {policies.map((p) => (
                <div
                  key={p.policy_id}
                  className="rounded border border-gray-200 dark:border-gray-700 p-3"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      {p.description && (
                        <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">
                          {p.description}
                        </p>
                      )}
                      <pre className="text-xs font-mono text-gray-800 dark:text-gray-200 whitespace-pre-wrap break-all bg-gray-50 dark:bg-gray-800 rounded p-2">
                        {p.definition}
                      </pre>
                    </div>
                    <button
                      onClick={() => handleDeletePolicy(p.policy_id)}
                      disabled={deleting === p.policy_id}
                      className="shrink-0 rounded border border-red-300 dark:border-red-800 px-2 py-0.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 disabled:opacity-50"
                    >
                      {deleting === p.policy_id ? "..." : "Delete"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="border-t border-gray-200 dark:border-gray-700 pt-3">
            <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">
              Add policy
            </p>
            <CreatePolicyForm
              engineId={engine.policy_engine_id}
              onCreated={loadPolicies}
            />
          </div>
        </div>
      )}
    </div>
  );
}

export default function PolicyManager() {
  const [engines, setEngines] = useState<PolicyEngineInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");

  const loadEngines = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.listPolicyEngines();
      setEngines(resp.policy_engines);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load policy engines",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadEngines();
  }, [loadEngines]);

  const handleCreateEngine = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!newName.trim()) return;
      setCreating(true);
      try {
        await api.createPolicyEngine(newName.trim(), newDesc.trim());
        setNewName("");
        setNewDesc("");
        loadEngines();
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to create engine",
        );
      } finally {
        setCreating(false);
      }
    },
    [newName, newDesc, loadEngines],
  );

  if (loading) return <LoadingSpinner className="mt-32" />;

  return (
    <div className="max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            Policy Enforcement
          </h1>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            Cedar policies evaluated against every request. Default-deny when
            enforcement is active.
          </p>
        </div>
      </div>

      {error && (
        <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      {/* Engines */}
      <div className="space-y-3">
        {engines.map((engine) => (
          <EngineSection
            key={engine.policy_engine_id}
            engine={engine}
            onRefresh={loadEngines}
          />
        ))}
      </div>

      {engines.length === 0 && (
        <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
          No policy engines. Create one to start defining Cedar policies.
        </p>
      )}

      {/* Create engine */}
      <div className="rounded-lg border border-gray-200 dark:border-gray-800 p-4">
        <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">
          Create policy engine
        </p>
        <form onSubmit={handleCreateEngine} className="flex gap-2">
          <input
            required
            placeholder="Engine name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            className="flex-1 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono"
          />
          <input
            placeholder="Description (optional)"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
            className="flex-1 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm"
          />
          <button
            type="submit"
            disabled={creating || !newName.trim()}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {creating ? "Creating..." : "Create"}
          </button>
        </form>
      </div>
    </div>
  );
}
