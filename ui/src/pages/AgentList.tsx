import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AgentSpec, PrimitiveConfig, UpdateAgentRequest } from "../api/types";
import LoadingSpinner from "../components/LoadingSpinner";
import PrimitivesSelector from "../components/PrimitivesSelector";
import { useAgents } from "../hooks/useAgents";

interface AgentFormProps {
  /** If provided, the form is in edit mode for this agent. */
  initial?: AgentSpec;
  onDone: () => void;
  onCancel: () => void;
}

function AgentForm({ initial, onDone, onCancel }: AgentFormProps) {
  const isEdit = !!initial;
  const [name, setName] = useState(initial?.name ?? "");
  const [model, setModel] = useState(initial?.model ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState(initial?.system_prompt ?? "");
  const [maxTurns, setMaxTurns] = useState(initial?.max_turns ?? 20);
  const [temperature, setTemperature] = useState(initial?.temperature ?? 1.0);
  const [primitives, setPrimitives] = useState<Record<string, PrimitiveConfig>>(
    initial?.primitives ?? {},
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      setSubmitting(true);
      try {
        if (isEdit) {
          const updates: UpdateAgentRequest = {
            model,
            description,
            system_prompt: systemPrompt,
            max_turns: maxTurns,
            temperature,
            primitives,
          };
          await api.updateAgent(name, updates);
        } else {
          await api.createAgent({
            name,
            model,
            description: description || undefined,
            system_prompt: systemPrompt || undefined,
            max_turns: maxTurns,
            temperature,
            primitives,
          });
        }
        onDone();
      } catch (err) {
        setError(err instanceof Error ? err.message : `Failed to ${isEdit ? "update" : "create"} agent`);
      } finally {
        setSubmitting(false);
      }
    },
    [name, model, description, systemPrompt, maxTurns, temperature, primitives, isEdit, onDone],
  );

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-lg border border-gray-200 dark:border-gray-800 p-4 space-y-3"
    >
      <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
        {isEdit ? `Edit ${name}` : "Create Agent"}
      </div>
      {error && (
        <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
      )}
      <div className="grid grid-cols-2 gap-3">
        <input
          required
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={isEdit}
          className={`rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono ${isEdit ? "opacity-50 cursor-not-allowed" : ""}`}
        />
        <input
          required
          placeholder="Model (e.g. anthropic.claude-sonnet-4-20250514-v1:0)"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          className="rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono"
        />
      </div>
      <input
        placeholder="Description (optional)"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm"
      />
      <textarea
        placeholder="System prompt (optional)"
        value={systemPrompt}
        onChange={(e) => setSystemPrompt(e.target.value)}
        rows={3}
        className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono"
      />
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Max Turns</label>
          <input
            type="number"
            min={1}
            max={100}
            value={maxTurns}
            onChange={(e) => setMaxTurns(Number(e.target.value))}
            className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Temperature</label>
          <input
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={temperature}
            onChange={(e) => setTemperature(Number(e.target.value))}
            className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono"
          />
        </div>
      </div>
      <PrimitivesSelector value={primitives} onChange={setPrimitives} excludeAgent={isEdit ? name : undefined} />
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {submitting ? (isEdit ? "Saving..." : "Creating...") : isEdit ? "Save" : "Create"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded border border-gray-300 dark:border-gray-700 px-3 py-1.5 text-sm text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

export default function AgentList() {
  const { agents, loading, error, refresh } = useAgents();
  const [deleting, setDeleting] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);

  const handleDelete = useCallback(
    async (name: string) => {
      if (!confirm(`Delete agent "${name}"?`)) return;
      setDeleting(name);
      try {
        await api.deleteAgent(name);
        refresh();
      } catch {
        // error handling via refresh
      } finally {
        setDeleting(null);
      }
    },
    [refresh],
  );

  if (loading) return <LoadingSpinner className="mt-32" />;

  return (
    <div className="max-w-4xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Agents
        </h1>
        {!creating && (
          <button
            onClick={() => { setCreating(true); setEditing(null); }}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
          >
            Create Agent
          </button>
        )}
      </div>

      {error && (
        <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      {creating && (
        <AgentForm
          onDone={() => { setCreating(false); refresh(); }}
          onCancel={() => setCreating(false)}
        />
      )}

      {agents.length === 0 && !creating ? (
        <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
          No agents configured. Create one to get started.
        </p>
      ) : (
        <div className="space-y-2">
          {agents.map((agent) => (
            <div key={agent.name}>
              {editing === agent.name ? (
                <AgentForm
                  initial={agent}
                  onDone={() => { setEditing(null); refresh(); }}
                  onCancel={() => setEditing(null)}
                />
              ) : (
                <div className="rounded-lg border border-gray-200 dark:border-gray-800 px-4 py-3">
                  <div className="flex items-center justify-between gap-4">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm font-medium text-gray-900 dark:text-gray-100">
                          {agent.name}
                        </span>
                        {agent.description && (
                          <span className="text-xs text-gray-400 dark:text-gray-500 truncate">
                            {agent.description}
                          </span>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5 mt-1">
                        <span className="font-mono text-[11px] text-gray-500 dark:text-gray-400">
                          {agent.model}
                        </span>
                        {Object.entries(agent.primitives)
                          .filter(([, v]) => v.enabled)
                          .map(([p]) => (
                            <span
                              key={p}
                              className="rounded bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 text-[10px] font-mono text-gray-500 dark:text-gray-400"
                            >
                              {p}
                            </span>
                          ))}
                        <span className="text-[10px] text-gray-400 dark:text-gray-500">
                          max_turns={agent.max_turns}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <Link
                        to={`/agents/${agent.name}/chat`}
                        className="rounded bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-indigo-700"
                      >
                        Chat
                      </Link>
                      <button
                        onClick={() => { setEditing(agent.name); setCreating(false); }}
                        className="rounded border border-gray-300 dark:border-gray-700 px-2.5 py-1 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(agent.name)}
                        disabled={deleting === agent.name}
                        className="rounded border border-red-300 dark:border-red-800 px-2.5 py-1 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 disabled:opacity-50"
                      >
                        {deleting === agent.name ? "..." : "Delete"}
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
