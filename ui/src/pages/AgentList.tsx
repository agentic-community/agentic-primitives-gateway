import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AgentSpec, PrimitiveConfig, UpdateAgentRequest } from "../api/types";
import LoadingSpinner from "../components/LoadingSpinner";
import PrimitivesSelector from "../components/PrimitivesSelector";
import SharedWithInput from "../components/SharedWithInput";
import { useAgents } from "../hooks/useAgents";

type FormMode = "create" | "edit" | "fork";

interface AgentFormProps {
  /** If provided, the form is prefilled from this spec. */
  initial?: AgentSpec;
  /** ``"create"`` = POST /agents; ``"edit"`` = PUT /agents/{name};
   *  ``"fork"`` = POST /agents/{source}/fork then optionally
   *  POST /versions to apply any other edits. */
  mode?: FormMode;
  onDone: () => void;
  onCancel: () => void;
}

function AgentForm({ initial, mode: modeProp, onDone, onCancel }: AgentFormProps) {
  const mode: FormMode = modeProp ?? (initial ? "edit" : "create");
  const isEdit = mode === "edit";
  const isFork = mode === "fork";
  const [name, setName] = useState(initial?.name ?? "");
  const [model, setModel] = useState(initial?.model ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState(initial?.system_prompt ?? "");
  const [maxTurns, setMaxTurns] = useState(initial?.max_turns ?? 20);
  const [temperature, setTemperature] = useState(initial?.temperature ?? 1.0);
  const [primitives, setPrimitives] = useState<Record<string, PrimitiveConfig>>(
    initial?.primitives ?? {},
  );
  const [providerOverrides, setProviderOverrides] = useState<Record<string, string>>(
    initial?.provider_overrides ?? {},
  );
  const [sharedWith, setSharedWith] = useState<string[]>(initial?.shared_with ?? []);
  const [checkpointingEnabled, setCheckpointingEnabled] = useState(initial?.checkpointing_enabled ?? false);
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
            provider_overrides: providerOverrides,
            shared_with: sharedWith,
            checkpointing_enabled: checkpointingEnabled,
          };
          await api.updateAgent(name, updates);
        } else if (isFork && initial) {
          // Fork the source identity into the caller's namespace under the
          // (possibly renamed) ``name``.  Then, if any other field was
          // edited in the form, layer a new version on top of the freshly
          // forked v1 so the user sees their edits immediately.
          const sourceQualified = `${initial.owner_id}:${initial.name}`;
          const forked = await api.forkAgent(sourceQualified, {
            target_name: name,
          });
          const edited =
            model !== initial.model ||
            description !== initial.description ||
            systemPrompt !== initial.system_prompt ||
            maxTurns !== initial.max_turns ||
            temperature !== initial.temperature ||
            JSON.stringify(primitives) !== JSON.stringify(initial.primitives) ||
            JSON.stringify(providerOverrides) !== JSON.stringify(initial.provider_overrides) ||
            JSON.stringify(sharedWith) !== JSON.stringify(initial.shared_with) ||
            checkpointingEnabled !== initial.checkpointing_enabled;
          if (edited) {
            const targetQualified = `${forked.owner_id}:${forked.agent_name}`;
            await api.createAgentVersion(targetQualified, {
              model,
              description,
              system_prompt: systemPrompt,
              max_turns: maxTurns,
              temperature,
              primitives,
              provider_overrides: providerOverrides,
              shared_with: sharedWith,
              checkpointing_enabled: checkpointingEnabled,
              commit_message: "post-fork edits",
            });
          }
        } else {
          await api.createAgent({
            name,
            model,
            description: description || undefined,
            system_prompt: systemPrompt || undefined,
            max_turns: maxTurns,
            temperature,
            primitives,
            provider_overrides: Object.keys(providerOverrides).length > 0 ? providerOverrides : undefined,
            shared_with: sharedWith,
            checkpointing_enabled: checkpointingEnabled,
          });
        }
        onDone();
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : `Failed to ${isEdit ? "update" : isFork ? "fork" : "create"} agent`,
        );
      } finally {
        setSubmitting(false);
      }
    },
    [name, model, description, systemPrompt, maxTurns, temperature, primitives, providerOverrides, sharedWith, checkpointingEnabled, isEdit, isFork, initial, onDone],
  );

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-lg border border-gray-200 dark:border-gray-800 p-4 space-y-3"
    >
      <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
        {isEdit
          ? `Edit ${name}`
          : isFork && initial
            ? `Fork ${initial.owner_id}:${initial.name}`
            : "Create Agent"}
      </div>
      {isFork && (
        <p className="text-[11px] text-gray-500 dark:text-gray-400">
          Forking will create a new agent in your namespace.  Rename it if
          you want, and any field you change here lands in the fork's
          first new version.
        </p>
      )}
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
      <PrimitivesSelector
        value={primitives}
        onChange={setPrimitives}
        providerOverrides={providerOverrides}
        onProviderOverridesChange={setProviderOverrides}
        excludeAgent={isEdit ? name : undefined}
      />
      <SharedWithInput value={sharedWith} onChange={setSharedWith} ownerId={isEdit ? initial?.owner_id : undefined} />
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={checkpointingEnabled}
          onChange={(e) => setCheckpointingEnabled(e.target.checked)}
          className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 h-3.5 w-3.5"
        />
        <span className="text-xs text-gray-500 dark:text-gray-400">
          Enable checkpointing
          <span className="text-[10px] text-gray-400 dark:text-gray-500 ml-1">(durable runs survive server restarts)</span>
        </span>
      </label>
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {submitting
            ? isEdit
              ? "Saving..."
              : isFork
                ? "Forking..."
                : "Creating..."
            : isEdit
              ? "Save"
              : isFork
                ? "Fork"
                : "Create"}
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
  // When set, we render the AgentForm in "fork" mode prefilled from this
  // agent.  The fork request doesn't fire until the user clicks Save.
  const [forking, setForking] = useState<AgentSpec | null>(null);

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
            <div key={`${agent.owner_id}:${agent.name}`}>
              {editing === agent.name ? (
                <AgentForm
                  initial={agent}
                  mode="edit"
                  onDone={() => { setEditing(null); refresh(); }}
                  onCancel={() => setEditing(null)}
                />
              ) : forking &&
                forking.owner_id === agent.owner_id &&
                forking.name === agent.name ? (
                <AgentForm
                  initial={agent}
                  mode="fork"
                  onDone={() => { setForking(null); refresh(); }}
                  onCancel={() => setForking(null)}
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
                        {agent.shared_with?.length > 0 ? (
                          agent.shared_with.includes("*") ? (
                            <span className="rounded bg-green-100 dark:bg-green-900/30 px-1.5 py-0.5 text-[10px] font-mono text-green-600 dark:text-green-400">
                              public
                            </span>
                          ) : (
                            agent.shared_with.map((g) => (
                              <span
                                key={g}
                                className="rounded bg-indigo-100 dark:bg-indigo-900/30 px-1.5 py-0.5 text-[10px] font-mono text-indigo-600 dark:text-indigo-400"
                              >
                                {g}
                              </span>
                            ))
                          )
                        ) : (
                          <span className="rounded bg-yellow-100 dark:bg-yellow-900/30 px-1.5 py-0.5 text-[10px] font-mono text-yellow-600 dark:text-yellow-400">
                            private
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <Link
                        to={`/agents/${agent.owner_id}:${agent.name}/versions`}
                        className="rounded border border-gray-300 dark:border-gray-700 px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                        title="Version history"
                      >
                        Versions
                      </Link>
                      <Link
                        to={`/agents/${agent.owner_id}:${agent.name}/lineage`}
                        className="rounded border border-gray-300 dark:border-gray-700 px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                        title="Lineage graph"
                      >
                        Lineage
                      </Link>
                      <button
                        type="button"
                        onClick={() => {
                          setForking(agent);
                          setEditing(null);
                          setCreating(false);
                        }}
                        className="rounded border border-gray-300 dark:border-gray-700 px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                        title="Fork into my namespace"
                      >
                        Fork
                      </button>
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
