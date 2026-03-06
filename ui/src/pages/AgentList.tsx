import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { CreateAgentRequest } from "../api/types";
import LoadingSpinner from "../components/LoadingSpinner";
import { useAgents } from "../hooks/useAgents";

function CreateAgentForm({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [model, setModel] = useState("");
  const [description, setDescription] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [primitivesJson, setPrimitivesJson] = useState("{}");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      setSubmitting(true);
      try {
        let primitives = {};
        try {
          primitives = JSON.parse(primitivesJson);
        } catch {
          throw new Error("Invalid JSON in primitives field");
        }
        const req: CreateAgentRequest = {
          name,
          model,
          description: description || undefined,
          system_prompt: systemPrompt || undefined,
          primitives,
        };
        await api.createAgent(req);
        setOpen(false);
        setName("");
        setModel("");
        setDescription("");
        setSystemPrompt("");
        setPrimitivesJson("{}");
        onCreated();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to create agent");
      } finally {
        setSubmitting(false);
      }
    },
    [name, model, description, systemPrompt, primitivesJson, onCreated],
  );

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
      >
        Create Agent
      </button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-lg border border-gray-200 dark:border-gray-800 p-4 space-y-3"
    >
      {error && (
        <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
      )}
      <div className="grid grid-cols-2 gap-3">
        <input
          required
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono"
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
        rows={2}
        className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm"
      />
      <textarea
        placeholder='Primitives JSON (e.g. {"memory": {"enabled": true}})'
        value={primitivesJson}
        onChange={(e) => setPrimitivesJson(e.target.value)}
        rows={3}
        className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-sm font-mono"
      />
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting}
          className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {submitting ? "Creating..." : "Create"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
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
        <CreateAgentForm onCreated={refresh} />
      </div>

      {error && (
        <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      {agents.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
          No agents configured. Create one to get started.
        </p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-gray-200 dark:border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-900 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
              <tr>
                <th className="px-4 py-2">Name</th>
                <th className="px-4 py-2">Description</th>
                <th className="px-4 py-2">Model</th>
                <th className="px-4 py-2">Max Turns</th>
                <th className="px-4 py-2">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-800">
              {agents.map((agent) => (
                <tr key={agent.name}>
                  <td className="px-4 py-2 font-mono font-medium text-gray-900 dark:text-gray-100">
                    {agent.name}
                  </td>
                  <td className="px-4 py-2 text-gray-600 dark:text-gray-400 max-w-xs truncate">
                    {agent.description || "-"}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-gray-600 dark:text-gray-400">
                    {agent.model}
                  </td>
                  <td className="px-4 py-2 text-gray-600 dark:text-gray-400">
                    {agent.max_turns}
                  </td>
                  <td className="px-4 py-2">
                    <div className="flex gap-2">
                      <Link
                        to={`/agents/${agent.name}/chat`}
                        className="rounded bg-indigo-600 px-2 py-0.5 text-xs font-medium text-white hover:bg-indigo-700"
                      >
                        Chat
                      </Link>
                      <button
                        onClick={() => handleDelete(agent.name)}
                        disabled={deleting === agent.name}
                        className="rounded border border-red-300 dark:border-red-800 px-2 py-0.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 disabled:opacity-50"
                      >
                        {deleting === agent.name ? "..." : "Delete"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
