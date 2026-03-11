import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api/client";
import type { TeamSpec, TeamStreamEvent } from "../api/types";
import ChatInput from "../components/ChatInput";
import LoadingSpinner from "../components/LoadingSpinner";

interface TaskInfo {
  id: string;
  title: string;
  status: string;
  suggestedWorker?: string;
  agent?: string;
  result?: string;
  error?: string;
  streamContent?: string;
}

function parseSSE(chunk: string): TeamStreamEvent[] {
  const events: TeamStreamEvent[] = [];
  for (const line of chunk.split("\n")) {
    if (line.startsWith("data: ")) {
      try {
        events.push(JSON.parse(line.slice(6)));
      } catch {
        // skip
      }
    }
  }
  return events;
}

const phaseLabels: Record<string, string> = {
  planning: "Planning",
  execution: "Executing",
  replanning: "Re-planning",
  synthesis: "Synthesizing",
  done: "Done",
};

const statusColors: Record<string, string> = {
  pending: "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400",
  claimed: "bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400",
  in_progress: "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400",
  done: "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400",
  failed: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400",
};

function TaskCard({ task }: { task: TaskInfo }) {
  const [open, setOpen] = useState(false);
  const hasDetail = task.result || task.error || task.streamContent;

  // Auto-expand when task is in progress (streaming) or completes
  useEffect(() => {
    if (task.streamContent && task.status === "in_progress") setOpen(true);
    if (task.status === "done" || task.status === "failed") setOpen(true);
  }, [task.status, task.streamContent]);

  const agentLabel = task.agent ?? task.suggestedWorker;
  const isAssignedNotClaimed = !task.agent && task.suggestedWorker;

  return (
    <div className={`rounded border px-3 py-2 ${task.status === "done" ? "border-green-200 dark:border-green-900/50" : task.status === "failed" ? "border-red-200 dark:border-red-900/50" : "border-gray-200 dark:border-gray-700"}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className={`rounded px-1.5 py-0.5 text-[10px] font-mono font-medium ${statusColors[task.status] ?? statusColors.pending}`}>
              {task.status}
            </span>
            <span className="text-xs font-medium text-gray-700 dark:text-gray-300">
              {task.title}
            </span>
          </div>
          {agentLabel && (
            <span className={`text-[10px] font-mono ${isAssignedNotClaimed ? "text-indigo-400 dark:text-indigo-500" : "text-gray-400 dark:text-gray-500"}`}>
              {isAssignedNotClaimed ? `suggested: ${agentLabel}` : agentLabel}
            </span>
          )}
        </div>
        {hasDetail && (
          <button
            onClick={() => setOpen(!open)}
            className="text-[10px] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 shrink-0"
          >
            {open ? "hide" : "detail"}
          </button>
        )}
      </div>
      {open && hasDetail && (
        <div className="mt-1.5 border-t border-gray-100 dark:border-gray-800 pt-1.5 max-h-64 overflow-y-auto">
          {task.error ? (
            <p className="text-[11px] text-red-500 dark:text-red-400 whitespace-pre-wrap break-words">
              {task.error}
            </p>
          ) : (
            <div className="text-[11px] text-gray-600 dark:text-gray-400 prose prose-xs dark:prose-invert max-w-none prose-p:my-0.5 prose-pre:my-1 prose-ul:my-0.5 prose-ol:my-0.5 prose-li:my-0 prose-headings:my-1 prose-code:text-[11px]">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {task.result || task.streamContent || ""}
              </ReactMarkdown>
              {task.status === "in_progress" && task.streamContent && (
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse ml-1" />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const RUN_STORAGE_PREFIX = "team-run:";

export default function TeamRun() {
  const { name } = useParams<{ name: string }>();
  const [team, setTeam] = useState<TeamSpec | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [polling, setPolling] = useState(false);
  const [prompt, setPrompt] = useState<string>("");
  const [teamRunId, setTeamRunId] = useState<string>(() => {
    if (!name) return "";
    const params = new URLSearchParams(window.location.search);
    return params.get("run_id") || localStorage.getItem(RUN_STORAGE_PREFIX + name) || "";
  });
  const [phase, setPhase] = useState<string>("");
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [activityLog, setActivityLog] = useState<string[]>([]);
  const [response, setResponse] = useState<string>("");
  const [stats, setStats] = useState<{ created: number; completed: number; workers: string[] } | null>(null);
  const [showModal, setShowModal] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bgCheckRef = useRef(false);

  useEffect(() => {
    if (!name) return;
    api.getTeam(name).then(setTeam).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, [name]);

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [activityLog]);

  // Abort stream on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // Replay a list of recorded events to reconstruct UI state.
  const replayEvents = useCallback(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (events: Array<Record<string, any>>) => {
      let synthContent = "";
      const newTasks: TaskInfo[] = [];
      const logs: string[] = [];

      for (const event of events) {
        switch (event.type) {
          case "team_start":
            logs.push(`Team run started: ${event.team_run_id}`);
            break;
          case "phase_change":
            setPhase(event.phase);
            logs.push(`Phase: ${phaseLabels[event.phase] ?? event.phase}`);
            break;
          case "tasks_created":
            for (const t of event.tasks) {
              newTasks.push({ id: t.id, title: t.title, status: "pending", suggestedWorker: t.suggested_worker });
              logs.push(`  -> ${t.title}${t.suggested_worker ? ` [${t.suggested_worker}]` : ""}`);
            }
            logs.push(`${event.count} tasks created`);
            break;
          case "task_claimed": {
            const idx = newTasks.findIndex((t) => t.id === event.task_id);
            if (idx >= 0) newTasks[idx] = { ...newTasks[idx], status: "in_progress", agent: event.agent };
            logs.push(`[${event.agent}] claimed: ${event.title}`);
            break;
          }
          case "task_completed": {
            const idx = newTasks.findIndex((t) => t.id === event.task_id);
            if (idx >= 0) newTasks[idx] = { ...newTasks[idx], status: "done", result: event.result };
            logs.push(`[${event.agent}] completed: ${event.task_id}`);
            break;
          }
          case "task_failed": {
            const idx = newTasks.findIndex((t) => t.id === event.task_id);
            if (idx >= 0) newTasks[idx] = { ...newTasks[idx], status: "failed", error: event.error };
            logs.push(`[${event.agent}] failed: ${event.task_id} -- ${event.error}`);
            break;
          }
          case "worker_start":
            logs.push(`[${event.agent}] started looking for tasks`);
            break;
          case "worker_done":
            logs.push(`[${event.agent}] finished -- no more tasks`);
            break;
          case "worker_error":
            logs.push(`Worker ${event.agent} error: ${event.error}`);
            break;
          case "agent_token":
            if (event.agent === "synthesizer") {
              synthContent += event.content;
            } else if (event.task_id) {
              const idx = newTasks.findIndex((t) => t.id === event.task_id);
              if (idx >= 0) newTasks[idx] = { ...newTasks[idx], streamContent: (newTasks[idx].streamContent ?? "") + event.content };
            }
            break;
          case "done":
            if (event.response) synthContent = event.response;
            setPhase("done");
            setStats({ created: event.tasks_created, completed: event.tasks_completed, workers: event.workers_used ?? [] });
            logs.push(`Done -- ${event.tasks_completed}/${event.tasks_created} tasks completed`);
            break;
        }
      }

      setTasks(newTasks);
      setActivityLog(logs);
      if (synthContent) setResponse(synthContent);
    },
    [],
  );

  // Restore state from a previous run (background or completed)
  useEffect(() => {
    if (!name || !teamRunId || bgCheckRef.current || running) return;
    bgCheckRef.current = true;

    // Sync run_id to URL
    const url = new URL(window.location.href);
    if (url.searchParams.get("run_id") !== teamRunId) {
      url.searchParams.set("run_id", teamRunId);
      window.history.replaceState(null, "", url.toString());
    }

    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const POLL_INTERVAL = 3000;
    const MAX_POLLS = 30;

    async function pollEvents(attempt: number) {
      if (attempt >= MAX_POLLS) {
        setPolling(false);
        return;
      }
      try {
        const data = await api.getTeamRunEvents(name!, teamRunId);
        if (data.events && data.events.length > 0) {
          replayEvents(data.events);
        }
        if (data.status === "running") {
          setPolling(true);
          if (!stopped) timer = setTimeout(() => pollEvents(attempt + 1), POLL_INTERVAL);
          return;
        }
      } catch { /* ignore */ }
      setPolling(false);
    }

    // Fetch events and replay them to reconstruct full UI state
    api.getTeamRunEvents(name, teamRunId).then((data) => {
      if (data.events && data.events.length > 0) {
        replayEvents(data.events);
      }
      if (data.status === "running") {
        setPolling(true);
        if (!stopped) timer = setTimeout(() => pollEvents(0), POLL_INTERVAL);
      }
    }).catch(() => {});

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [name, teamRunId, running, replayEvents]);

  const addLog = useCallback((msg: string) => {
    setActivityLog((prev) => [...prev, msg]);
  }, []);

  const handleSend = useCallback(
    async (message: string) => {
      if (!name) return;
      setRunning(true);
      setPrompt(message);
      setPhase("");
      setTasks([]);
      setActivityLog([]);
      setResponse("");
      setStats(null);
      setTeamRunId("");
      setPolling(false);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const stream = api.runTeamStream(name, { message }, controller.signal);
        const reader = stream.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let synthContent = "";

        while (true) {
          const { done: streamDone, value } = await reader.read();
          if (streamDone) break;

          buffer += typeof value === "string" ? value : decoder.decode(value as Uint8Array, { stream: true });
          const events = parseSSE(buffer);
          const lastNewline = buffer.lastIndexOf("\n");
          buffer = lastNewline >= 0 ? buffer.slice(lastNewline + 1) : buffer;

          for (const event of events) {
            switch (event.type) {
              case "team_start":
                setTeamRunId(event.team_run_id);
                localStorage.setItem(RUN_STORAGE_PREFIX + name, event.team_run_id);
                {
                  const url = new URL(window.location.href);
                  url.searchParams.set("run_id", event.team_run_id);
                  window.history.replaceState(null, "", url.toString());
                }
                addLog(`Team run started: ${event.team_run_id}`);
                break;
              case "phase_change":
                setPhase(event.phase);
                addLog(`Phase: ${phaseLabels[event.phase] ?? event.phase}`);
                break;
              case "tasks_created":
                setTasks((prev) => [
                  ...prev,
                  ...event.tasks.map((t) => ({ id: t.id, title: t.title, status: "pending", suggestedWorker: t.suggested_worker })),
                ]);
                addLog(`${event.count} tasks created:`);
                for (const t of event.tasks) {
                  addLog(`  -> ${t.title}${t.suggested_worker ? ` [${t.suggested_worker}]` : ""}`);
                }
                break;
              case "task_claimed":
                setTasks((prev) => prev.map((t) =>
                  t.id === event.task_id ? { ...t, status: "in_progress", agent: event.agent } : t,
                ));
                addLog(`[${event.agent}] claimed: ${event.title}`);
                break;
              case "task_completed":
                setTasks((prev) => prev.map((t) =>
                  t.id === event.task_id ? { ...t, status: "done", result: event.result } : t,
                ));
                addLog(`[${event.agent}] completed: ${event.task_id}`);
                break;
              case "task_failed":
                setTasks((prev) => prev.map((t) =>
                  t.id === event.task_id ? { ...t, status: "failed", error: event.error } : t,
                ));
                addLog(`[${event.agent}] failed: ${event.task_id} -- ${event.error}`);
                break;
              case "worker_start":
                addLog(`[${event.agent}] started looking for tasks`);
                break;
              case "worker_done":
                addLog(`[${event.agent}] finished -- no more tasks`);
                break;
              case "worker_error":
                addLog(`Worker ${event.agent} error: ${event.error}`);
                break;
              case "agent_token":
                if (event.agent === "synthesizer") {
                  synthContent += event.content;
                  setResponse(synthContent);
                } else if (event.task_id) {
                  setTasks((prev) => prev.map((t) =>
                    t.id === event.task_id ? { ...t, streamContent: (t.streamContent ?? "") + event.content } : t,
                  ));
                }
                break;
              case "done":
                if (event.response) setResponse(event.response);
                setPhase("done");
                setStats({
                  created: event.tasks_created,
                  completed: event.tasks_completed,
                  workers: event.workers_used,
                });
                addLog(`Done -- ${event.tasks_completed}/${event.tasks_created} tasks completed`);
                break;
            }
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        addLog(`Error: ${err instanceof Error ? err.message : "Request failed"}`);
      } finally {
        abortRef.current = null;
        setRunning(false);
      }
    },
    [name, addLog],
  );

  if (loading) return <LoadingSpinner className="mt-32" />;
  if (error || !team) {
    return (
      <div className="mt-32 text-center text-sm text-red-600 dark:text-red-400">
        {error ?? "Team not found"}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-800 pb-3 mb-4">
        <div>
          <div className="flex items-center gap-2">
            <Link
              to="/teams"
              className="text-xs text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
            >
              Teams /
            </Link>
            <h1 className="text-sm font-semibold font-mono text-gray-900 dark:text-gray-100">
              {team.name}
            </h1>
          </div>
          <div className="flex items-center gap-3 mt-1 text-[11px] text-gray-500 dark:text-gray-400">
            <span className="font-mono">planner: {team.planner}</span>
            <span>|</span>
            <span className="font-mono">synth: {team.synthesizer}</span>
            <span>|</span>
            <span>workers: {team.workers.join(", ")}</span>
            {teamRunId && (
              <>
                <span>|</span>
                <button
                  className="font-mono hover:text-gray-700 dark:hover:text-gray-300"
                  onClick={() => navigator.clipboard.writeText(teamRunId)}
                >
                  run: {teamRunId.slice(0, 8)}...
                </button>
              </>
            )}
            {phase && (
              <>
                <span>|</span>
                <span className={`font-medium ${phase === "done" ? "text-green-500" : "text-indigo-500"}`}>
                  {phaseLabels[phase] ?? phase}
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Prompt */}
      {prompt && (
        <div className="rounded-lg bg-indigo-50 dark:bg-indigo-950/30 border border-indigo-200 dark:border-indigo-900/50 px-4 py-2.5 mb-4">
          <p className="text-[11px] font-medium text-indigo-500 dark:text-indigo-400 mb-0.5">Prompt</p>
          <p className="text-sm text-gray-800 dark:text-gray-200">{prompt}</p>
        </div>
      )}

      {/* Main content: task board + activity */}
      <div className="flex-1 overflow-hidden grid grid-cols-2 gap-4 mb-4">
        {/* Task Board */}
        <div className="overflow-y-auto space-y-2">
          <h2 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
            Task Board {tasks.length > 0 && `(${tasks.filter((t) => t.status === "done").length}/${tasks.length})`}
          </h2>
          {tasks.length === 0 && !running && !polling && (
            <p className="text-sm text-gray-400 dark:text-gray-500 py-8 text-center">
              Send a message to start a team run
            </p>
          )}
          {tasks.length === 0 && running && phase === "planning" && (
            <div className="flex items-center gap-2 text-xs text-gray-400 py-4">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse" />
              Planner is creating tasks...
            </div>
          )}
          {polling && (
            <div className="flex items-center gap-2 text-xs text-gray-400 py-4">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse" />
              Team is working in the background...
            </div>
          )}
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} />
          ))}
        </div>

        {/* Activity Log + Response */}
        <div className="overflow-y-auto space-y-3">
          <h2 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
            Activity
          </h2>
          <div ref={logRef} className="space-y-0.5 max-h-48 overflow-y-auto">
            {activityLog.map((msg, i) => (
              <p key={i} className="text-[11px] text-gray-500 dark:text-gray-400 font-mono">
                {msg}
              </p>
            ))}
            {activityLog.length === 0 && (
              <p className="text-xs text-gray-400 py-4 text-center">No activity yet</p>
            )}
          </div>

          {response && (
            <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Response
                </h2>
                <div className="flex gap-1.5">
                  <button
                    onClick={() => setShowModal(true)}
                    className="rounded border border-gray-300 dark:border-gray-700 px-2 py-0.5 text-[10px] text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                  >
                    Expand
                  </button>
                  <button
                    onClick={() => {
                      const blob = new Blob([response], { type: "text/markdown" });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = `${name}-response.md`;
                      a.click();
                      URL.revokeObjectURL(url);
                    }}
                    className="rounded border border-gray-300 dark:border-gray-700 px-2 py-0.5 text-[10px] text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                  >
                    Save .md
                  </button>
                </div>
              </div>
              <div className="rounded-lg bg-gray-50 dark:bg-gray-900 px-4 py-3 text-sm prose prose-sm dark:prose-invert max-w-none max-h-64 overflow-y-auto">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {response}
                </ReactMarkdown>
              </div>
            </div>
          )}

          {stats && (
            <div className="text-[11px] text-gray-400 dark:text-gray-500 space-y-0.5">
              <p>Tasks: {stats.completed}/{stats.created} completed</p>
              <p>Workers: {stats.workers.join(", ") || "none"}</p>
            </div>
          )}
        </div>
      </div>

      {/* Input */}
      <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
        <ChatInput onSend={handleSend} disabled={running || polling} />
      </div>

      {/* Expanded response modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowModal(false)}>
          <div
            className="bg-white dark:bg-gray-900 rounded-lg shadow-xl max-w-4xl w-full mx-4 max-h-[90vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-800 px-6 py-3">
              <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Response</h2>
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    const blob = new Blob([response], { type: "text/markdown" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `${name}-response.md`;
                    a.click();
                    URL.revokeObjectURL(url);
                  }}
                  className="rounded border border-gray-300 dark:border-gray-700 px-3 py-1 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                >
                  Save .md
                </button>
                <button
                  onClick={() => setShowModal(false)}
                  className="rounded border border-gray-300 dark:border-gray-700 px-3 py-1 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                >
                  Close
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto px-6 py-4 prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {response}
              </ReactMarkdown>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
