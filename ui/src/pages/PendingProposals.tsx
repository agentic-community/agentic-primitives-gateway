import { useState } from "react";
import { api } from "../api/client";
import type { AgentVersion, TeamVersion } from "../api/types";
import LoadingSpinner from "../components/LoadingSpinner";
import { useAgentProposals, useTeamProposals } from "../hooks/useProposals";
import { cn } from "../lib/cn";

type Tab = "agents" | "teams";

function formatTime(ts: string): string {
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

export default function PendingProposals() {
  const [tab, setTab] = useState<Tab>("agents");
  return (
    <div className="p-6 max-w-6xl mx-auto">
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Pending proposals
        </h1>
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Version deploys waiting for admin approval (admin-approval gate
          must be enabled in config).
        </p>
      </header>
      <div className="mb-3 inline-flex rounded border border-gray-200 dark:border-gray-800 text-xs">
        <button
          type="button"
          onClick={() => setTab("agents")}
          className={cn(
            "px-3 py-1.5",
            tab === "agents"
              ? "bg-indigo-600 text-white"
              : "text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-900/40",
          )}
        >
          Agents
        </button>
        <button
          type="button"
          onClick={() => setTab("teams")}
          className={cn(
            "px-3 py-1.5",
            tab === "teams"
              ? "bg-indigo-600 text-white"
              : "text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-900/40",
          )}
        >
          Teams
        </button>
      </div>
      {tab === "agents" ? <AgentsTab /> : <TeamsTab />}
    </div>
  );
}

function AgentsTab() {
  const { proposals, loading, error, refresh } = useAgentProposals();
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [rejectId, setRejectId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const act = async (v: AgentVersion, fn: () => Promise<unknown>) => {
    setBusy(v.version_id);
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

  const qualified = (v: AgentVersion) => `${v.owner_id}:${v.agent_name}`;

  return (
    <>
      {actionError && (
        <div className="mb-3 text-xs rounded border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/40 text-red-700 dark:text-red-300 px-3 py-2">
          {actionError}
        </div>
      )}
      {loading && <LoadingSpinner />}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
      {!loading && proposals.length === 0 && (
        <p className="text-sm text-gray-500">No pending agent proposals.</p>
      )}
      <div className="space-y-1">
        {proposals.map((v) => (
          <ProposalRow
            key={v.version_id}
            identity={qualified(v)}
            label={`v${v.version_number} — ${v.agent_name}`}
            createdBy={v.created_by}
            createdAt={v.created_at}
            commitMessage={v.commit_message}
            busy={busy === v.version_id}
            rejecting={rejectId === v.version_id}
            rejectReason={rejectReason}
            setRejectReason={setRejectReason}
            onApprove={() => act(v, async () => {
              await api.approveAgentVersion(qualified(v), v.version_id);
              await api.deployAgentVersion(qualified(v), v.version_id);
            })}
            onRejectClick={() => setRejectId(rejectId === v.version_id ? null : v.version_id)}
            onRejectConfirm={() =>
              act(v, async () => {
                await api.rejectAgentVersion(qualified(v), v.version_id, rejectReason);
                setRejectId(null);
                setRejectReason("");
              })
            }
          />
        ))}
      </div>
    </>
  );
}

function TeamsTab() {
  const { proposals, loading, error, refresh } = useTeamProposals();
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [rejectId, setRejectId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const act = async (v: TeamVersion, fn: () => Promise<unknown>) => {
    setBusy(v.version_id);
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

  const qualified = (v: TeamVersion) => `${v.owner_id}:${v.team_name}`;

  return (
    <>
      {actionError && (
        <div className="mb-3 text-xs rounded border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/40 text-red-700 dark:text-red-300 px-3 py-2">
          {actionError}
        </div>
      )}
      {loading && <LoadingSpinner />}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
      {!loading && proposals.length === 0 && (
        <p className="text-sm text-gray-500">No pending team proposals.</p>
      )}
      <div className="space-y-1">
        {proposals.map((v) => (
          <ProposalRow
            key={v.version_id}
            identity={qualified(v)}
            label={`v${v.version_number} — ${v.team_name}`}
            createdBy={v.created_by}
            createdAt={v.created_at}
            commitMessage={v.commit_message}
            busy={busy === v.version_id}
            rejecting={rejectId === v.version_id}
            rejectReason={rejectReason}
            setRejectReason={setRejectReason}
            onApprove={() => act(v, async () => {
              await api.approveTeamVersion(qualified(v), v.version_id);
              await api.deployTeamVersion(qualified(v), v.version_id);
            })}
            onRejectClick={() => setRejectId(rejectId === v.version_id ? null : v.version_id)}
            onRejectConfirm={() =>
              act(v, async () => {
                await api.rejectTeamVersion(qualified(v), v.version_id, rejectReason);
                setRejectId(null);
                setRejectReason("");
              })
            }
          />
        ))}
      </div>
    </>
  );
}

interface RowProps {
  identity: string;
  label: string;
  createdBy: string;
  createdAt: string;
  commitMessage: string | null;
  busy: boolean;
  rejecting: boolean;
  rejectReason: string;
  setRejectReason: (s: string) => void;
  onApprove: () => void;
  onRejectClick: () => void;
  onRejectConfirm: () => void;
}

function ProposalRow({
  identity,
  label,
  createdBy,
  createdAt,
  commitMessage,
  busy,
  rejecting,
  rejectReason,
  setRejectReason,
  onApprove,
  onRejectClick,
  onRejectConfirm,
}: RowProps) {
  return (
    <div className="rounded border border-gray-200 dark:border-gray-800 px-3 py-2 text-xs font-mono">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="font-semibold text-gray-900 dark:text-gray-100">{label}</span>
        <span className="text-gray-500">{identity}</span>
        <span className="text-gray-500">by {createdBy}</span>
        <span className="text-gray-400">{formatTime(createdAt)}</span>
        {commitMessage && (
          <span className="text-gray-700 dark:text-gray-300 italic truncate max-w-xs">
            "{commitMessage}"
          </span>
        )}
        <span className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={onApprove}
            disabled={busy}
            className="px-2 py-1 text-[11px] rounded border border-green-300 dark:border-green-700 text-green-700 dark:text-green-300 hover:bg-green-50 dark:hover:bg-green-950/40 disabled:opacity-50"
          >
            Approve + deploy
          </button>
          <button
            type="button"
            onClick={onRejectClick}
            disabled={busy}
            className="px-2 py-1 text-[11px] rounded border border-red-300 dark:border-red-700 text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-950/40 disabled:opacity-50"
          >
            Reject
          </button>
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
            onClick={onRejectConfirm}
            disabled={busy || !rejectReason.trim()}
            className="px-2 py-1 text-[11px] rounded bg-red-600 text-white hover:bg-red-500 disabled:opacity-50"
          >
            Confirm reject
          </button>
        </div>
      )}
    </div>
  );
}
