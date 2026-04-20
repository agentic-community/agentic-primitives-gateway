import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  AgentSpec,
  CreateTeamRequest,
  TeamSpec,
  UpdateTeamRequest,
} from "../api/types";
import LoadingSpinner from "../components/LoadingSpinner";
import SharedWithInput from "../components/SharedWithInput";
import { useTeams } from "../hooks/useTeams";

type TeamFormMode = "create" | "edit" | "fork";

interface TeamFormProps {
  initial?: TeamSpec;
  /** ``"create"`` = POST /teams; ``"edit"`` = PUT /teams/{name};
   *  ``"fork"`` = POST /teams/{source}/fork + optional version update. */
  mode?: TeamFormMode;
  onDone: () => void;
  onCancel: () => void;
}

function AgentSelect({
  label,
  value,
  onChange,
  agents,
  multiple,
}: {
  label: string;
  value: string | string[];
  onChange: (v: string | string[]) => void;
  agents: AgentSpec[];
  multiple?: boolean;
}) {
  if (multiple) {
    const selected = value as string[];
    return (
      <div>
        <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
          {label}
        </label>
        <div className="rounded border border-gray-300 dark:border-gray-700 p-2 max-h-32 overflow-y-auto space-y-1">
          {agents.length === 0 && (
            <p className="text-xs text-gray-400">No agents available</p>
          )}
          {agents.map((a) => (
            <label key={a.name} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={selected.includes(a.name)}
                onChange={() => {
                  const next = selected.includes(a.name)
                    ? selected.filter((n) => n !== a.name)
                    : [...selected, a.name];
                  onChange(next);
                }}
                className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 h-3.5 w-3.5"
              />
              <span className="text-xs font-mono text-gray-700 dark:text-gray-300">
                {a.name}
              </span>
              {a.description && (
                <span className="text-[10px] text-gray-400 truncate">{a.description}</span>
              )}
            </label>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div>
      <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">
        {label}
      </label>
      <select
        value={value as string}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono"
      >
        <option value="">Select an agent...</option>
        {agents.map((a) => (
          <option key={a.name} value={a.name}>
            {a.name}
          </option>
        ))}
      </select>
    </div>
  );
}

function TeamForm({ initial, mode: modeProp, onDone, onCancel }: TeamFormProps) {
  const mode: TeamFormMode = modeProp ?? (initial ? "edit" : "create");
  const isEdit = mode === "edit";
  const isFork = mode === "fork";
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [planner, setPlanner] = useState(initial?.planner ?? "");
  const [synthesizer, setSynthesizer] = useState(initial?.synthesizer ?? "");
  const [workers, setWorkers] = useState<string[]>(initial?.workers ?? []);
  const [globalMaxTurns, setGlobalMaxTurns] = useState(initial?.global_max_turns ?? 100);
  const [globalTimeout, setGlobalTimeout] = useState(initial?.global_timeout_seconds ?? 300);
  const [sharedMemory, setSharedMemory] = useState(!!initial?.shared_memory_namespace);
  const [sharedWith, setSharedWith] = useState<string[]>(initial?.shared_with ?? []);
  const [checkpointingEnabled, setCheckpointingEnabled] = useState(initial?.checkpointing_enabled ?? false);
  const [agents, setAgents] = useState<AgentSpec[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listAgents().then(setAgents).catch(() => {});
  }, []);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      setSubmitting(true);
      try {
        if (isEdit) {
          const updates: UpdateTeamRequest = {
            description,
            planner,
            synthesizer,
            workers,
            global_max_turns: globalMaxTurns,
            global_timeout_seconds: globalTimeout,
            shared_memory_namespace: sharedMemory ? "team:{team_name}" : null,
            shared_with: sharedWith,
            checkpointing_enabled: checkpointingEnabled,
          };
          await api.updateTeam(name, updates);
        } else if (isFork && initial) {
          // Fork first, then layer edits on top as a new version if the
          // user changed anything beyond the name.
          const sourceQualified = `${initial.owner_id}:${initial.name}`;
          const forked = await api.forkTeam(sourceQualified, { target_name: name });
          const edited =
            description !== initial.description ||
            planner !== initial.planner ||
            synthesizer !== initial.synthesizer ||
            JSON.stringify(workers) !== JSON.stringify(initial.workers) ||
            globalMaxTurns !== initial.global_max_turns ||
            globalTimeout !== initial.global_timeout_seconds ||
            sharedMemory !== !!initial.shared_memory_namespace ||
            JSON.stringify(sharedWith) !== JSON.stringify(initial.shared_with) ||
            checkpointingEnabled !== initial.checkpointing_enabled;
          if (edited) {
            const targetQualified = `${forked.owner_id}:${forked.team_name}`;
            await api.createTeamVersion(targetQualified, {
              description,
              planner,
              synthesizer,
              workers,
              global_max_turns: globalMaxTurns,
              global_timeout_seconds: globalTimeout,
              shared_memory_namespace: sharedMemory ? "team:{team_name}" : null,
              shared_with: sharedWith,
              checkpointing_enabled: checkpointingEnabled,
              commit_message: "post-fork edits",
            });
          }
        } else {
          const req: CreateTeamRequest = {
            name,
            description: description || undefined,
            planner,
            synthesizer,
            workers,
            global_max_turns: globalMaxTurns,
            global_timeout_seconds: globalTimeout,
            shared_memory_namespace: sharedMemory ? "team:{team_name}" : undefined,
            shared_with: sharedWith,
            checkpointing_enabled: checkpointingEnabled,
          };
          await api.createTeam(req);
        }
        onDone();
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : `Failed to ${isEdit ? "update" : isFork ? "fork" : "create"} team`,
        );
      } finally {
        setSubmitting(false);
      }
    },
    [name, description, planner, synthesizer, workers, globalMaxTurns, globalTimeout, sharedMemory, sharedWith, checkpointingEnabled, isEdit, isFork, initial, onDone],
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
            : "Create Team"}
      </div>
      {isFork && (
        <p className="text-[11px] text-gray-500 dark:text-gray-400">
          Forking will create a new team in your namespace.  Rename it if
          you want, and any field you change here lands in the fork's
          first new version.
        </p>
      )}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}

      <div className="grid grid-cols-2 gap-3">
        <input
          required
          placeholder="Team name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={isEdit}
          className={`rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono ${isEdit ? "opacity-50 cursor-not-allowed" : ""}`}
        />
        <input
          placeholder="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm"
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <AgentSelect label="Planner" value={planner} onChange={(v) => setPlanner(v as string)} agents={agents} />
        <AgentSelect label="Synthesizer" value={synthesizer} onChange={(v) => setSynthesizer(v as string)} agents={agents} />
      </div>

      <AgentSelect label="Workers" value={workers} onChange={(v) => setWorkers(v as string[])} agents={agents} multiple />

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Global Max Turns</label>
          <input
            type="number"
            min={1}
            max={1000}
            value={globalMaxTurns}
            onChange={(e) => setGlobalMaxTurns(Number(e.target.value))}
            className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Timeout (seconds)</label>
          <input
            type="number"
            min={10}
            max={3600}
            value={globalTimeout}
            onChange={(e) => setGlobalTimeout(Number(e.target.value))}
            className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono"
          />
        </div>
      </div>

      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={sharedMemory}
          onChange={(e) => setSharedMemory(e.target.checked)}
          className="rounded border-gray-300 dark:border-gray-600 text-indigo-600 h-3.5 w-3.5"
        />
        <span className="text-xs text-gray-500 dark:text-gray-400">
          Enable shared memory
          <span className="text-[10px] text-gray-400 dark:text-gray-500 ml-1">(workers can share findings with each other)</span>
        </span>
      </label>

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
          disabled={submitting || !planner || !synthesizer || workers.length === 0}
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

export default function TeamList() {
  const { teams, loading, error, refresh } = useTeams();
  const [deleting, setDeleting] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  // Same pattern as AgentList: fork opens a prefilled form; no call to
  // the server until the user clicks Save.
  const [forking, setForking] = useState<TeamSpec | null>(null);

  const handleDelete = useCallback(
    async (name: string) => {
      if (!confirm(`Delete team "${name}"?`)) return;
      setDeleting(name);
      try {
        await api.deleteTeam(name);
        refresh();
      } catch {
        // handled via refresh
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
          Teams
        </h1>
        {!creating && (
          <button
            onClick={() => { setCreating(true); setEditing(null); }}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
          >
            Create Team
          </button>
        )}
      </div>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      {creating && (
        <TeamForm
          onDone={() => { setCreating(false); refresh(); }}
          onCancel={() => setCreating(false)}
        />
      )}

      {teams.length === 0 && !creating ? (
        <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
          No teams configured. Create one to get started.
        </p>
      ) : (
        <div className="space-y-2">
          {teams.map((team) => (
            <div key={`${team.owner_id}:${team.name}`}>
              {editing === team.name ? (
                <TeamForm
                  initial={team}
                  mode="edit"
                  onDone={() => { setEditing(null); refresh(); }}
                  onCancel={() => setEditing(null)}
                />
              ) : forking &&
                forking.owner_id === team.owner_id &&
                forking.name === team.name ? (
                <TeamForm
                  initial={team}
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
                          {team.name}
                        </span>
                        {team.description && (
                          <span className="text-xs text-gray-400 dark:text-gray-500 truncate">
                            {team.description}
                          </span>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5 mt-1">
                        <span className="rounded bg-blue-100 dark:bg-blue-900/30 px-1.5 py-0.5 text-[10px] font-mono text-blue-600 dark:text-blue-400">
                          planner: {team.planner}
                        </span>
                        <span className="rounded bg-green-100 dark:bg-green-900/30 px-1.5 py-0.5 text-[10px] font-mono text-green-600 dark:text-green-400">
                          synth: {team.synthesizer}
                        </span>
                        {team.workers.map((w) => (
                          <span
                            key={w}
                            className="rounded bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 text-[10px] font-mono text-gray-500 dark:text-gray-400"
                          >
                            {w}
                          </span>
                        ))}
                        {team.shared_with?.length > 0 ? (
                          team.shared_with.includes("*") ? (
                            <span className="rounded bg-green-100 dark:bg-green-900/30 px-1.5 py-0.5 text-[10px] font-mono text-green-600 dark:text-green-400">
                              public
                            </span>
                          ) : (
                            team.shared_with.map((g) => (
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
                        to={`/teams/${team.owner_id}:${team.name}/versions`}
                        className="rounded border border-gray-300 dark:border-gray-700 px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                        title="Version history"
                      >
                        Versions
                      </Link>
                      <Link
                        to={`/teams/${team.owner_id}:${team.name}/lineage`}
                        className="rounded border border-gray-300 dark:border-gray-700 px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                        title="Lineage graph"
                      >
                        Lineage
                      </Link>
                      <button
                        type="button"
                        onClick={() => {
                          setForking(team);
                          setEditing(null);
                          setCreating(false);
                        }}
                        className="rounded border border-gray-300 dark:border-gray-700 px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                        title="Fork into my namespace"
                      >
                        Fork
                      </button>
                      <Link
                        to={`/teams/${team.name}/run`}
                        className="rounded bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-indigo-700"
                      >
                        Run
                      </Link>
                      <button
                        onClick={() => api.exportTeam(team.name)}
                        className="rounded border border-gray-300 dark:border-gray-700 px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                        title="Export as Python script"
                      >
                        Export
                      </button>
                      <button
                        onClick={() => { setEditing(team.name); setCreating(false); }}
                        className="rounded border border-gray-300 dark:border-gray-700 px-2.5 py-1 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(team.name)}
                        disabled={deleting === team.name}
                        className="rounded border border-red-300 dark:border-red-800 px-2.5 py-1 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 disabled:opacity-50"
                      >
                        {deleting === team.name ? "..." : "Delete"}
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
