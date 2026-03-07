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
import { useTeams } from "../hooks/useTeams";

interface TeamFormProps {
  initial?: TeamSpec;
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

function TeamForm({ initial, onDone, onCancel }: TeamFormProps) {
  const isEdit = !!initial;
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [planner, setPlanner] = useState(initial?.planner ?? "");
  const [synthesizer, setSynthesizer] = useState(initial?.synthesizer ?? "");
  const [workers, setWorkers] = useState<string[]>(initial?.workers ?? []);
  const [globalMaxTurns, setGlobalMaxTurns] = useState(initial?.global_max_turns ?? 100);
  const [globalTimeout, setGlobalTimeout] = useState(initial?.global_timeout_seconds ?? 300);
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
          };
          await api.updateTeam(name, updates);
        } else {
          const req: CreateTeamRequest = {
            name,
            description: description || undefined,
            planner,
            synthesizer,
            workers,
            global_max_turns: globalMaxTurns,
            global_timeout_seconds: globalTimeout,
          };
          await api.createTeam(req);
        }
        onDone();
      } catch (err) {
        setError(err instanceof Error ? err.message : `Failed to ${isEdit ? "update" : "create"} team`);
      } finally {
        setSubmitting(false);
      }
    },
    [name, description, planner, synthesizer, workers, globalMaxTurns, globalTimeout, isEdit, onDone],
  );

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-lg border border-gray-200 dark:border-gray-800 p-4 space-y-3"
    >
      <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
        {isEdit ? `Edit ${name}` : "Create Team"}
      </div>
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

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting || !planner || !synthesizer || workers.length === 0}
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

export default function TeamList() {
  const { teams, loading, error, refresh } = useTeams();
  const [deleting, setDeleting] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);

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
            <div key={team.name}>
              {editing === team.name ? (
                <TeamForm
                  initial={team}
                  onDone={() => { setEditing(null); refresh(); }}
                  onCancel={() => setEditing(null)}
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
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      <Link
                        to={`/teams/${team.name}/run`}
                        className="rounded bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-indigo-700"
                      >
                        Run
                      </Link>
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
