import { useCallback, useEffect, useRef, useState } from "react";
import { useAutoScroll } from "../hooks/useAutoScroll";
import { Link, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api/client";
import type { TeamSpec, TeamStreamEvent } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import ChatInput from "../components/ChatInput";
import LoadingSpinner from "../components/LoadingSpinner";
import { parseSSE } from "../lib/sse";

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

const phaseLabels: Record<string, string> = {
  planning: "Planning",
  execution: "Executing",
  replanning: "Re-planning",
  synthesis: "Synthesizing",
  done: "Done",
  cancelled: "Cancelled",
};

const statusColors: Record<string, string> = {
  pending: "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400",
  claimed: "bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400",
  in_progress: "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400",
  done: "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400",
  failed: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400",
};

function TaskCard({ task, onRetry }: { task: TaskInfo; onRetry?: (taskId: string) => void }) {
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
        <div className="flex items-center gap-1.5 shrink-0">
          {task.status === "failed" && onRetry && (
            <button
              onClick={() => onRetry(task.id)}
              className="text-[10px] font-medium text-indigo-500 hover:text-indigo-700 dark:text-indigo-400 dark:hover:text-indigo-300"
            >
              retry
            </button>
          )}
          {hasDetail && (
            <button
              onClick={() => setOpen(!open)}
              className="text-[10px] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
            >
              {open ? "hide" : "detail"}
            </button>
          )}
        </div>
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

function runsKey(userId: string, teamName: string) {
  return `team-runs:${userId}:${teamName}`;
}

function getRuns(userId: string, teamName: string): string[] {
  try {
    return JSON.parse(localStorage.getItem(runsKey(userId, teamName)) || "[]");
  } catch {
    return [];
  }
}

function saveRuns(userId: string, teamName: string, runs: string[]) {
  localStorage.setItem(runsKey(userId, teamName), JSON.stringify(runs));
}

function addRun(userId: string, teamName: string, runId: string) {
  const runs = getRuns(userId, teamName);
  if (!runs.includes(runId)) {
    saveRuns(userId, teamName, [runId, ...runs]);
  }
}

function removeRun(userId: string, teamName: string, runId: string) {
  saveRuns(userId, teamName, getRuns(userId, teamName).filter((r) => r !== runId));
}

export default function TeamRun() {
  const { name } = useParams<{ name: string }>();
  const { user } = useAuth();
  const userId = user?.profile?.sub || "anonymous";
  const [team, setTeam] = useState<TeamSpec | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [polling, setPolling] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [prompt, setPrompt] = useState<string>("");
  const [teamRunId, setTeamRunId] = useState<string>(() => {
    if (!name) return "";
    const params = new URLSearchParams(window.location.search);
    // Only restore a run if explicitly specified in URL
    return params.get("run_id") || "";
  });
  const [phase, setPhase] = useState<string>("");
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [activityLog, setActivityLog] = useState<string[]>([]);
  const [response, setResponse] = useState<string>("");
  const [stats, setStats] = useState<{ created: number; completed: number; workers: string[] } | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [runList, setRunList] = useState<string[]>(() => name ? getRuns(userId, name) : []);
  const logRef = useAutoScroll([activityLog]);
  const abortRef = useRef<AbortController | null>(null);
  const bgCheckRef = useRef(false);
  const streamDoneRef = useRef(false);

  useEffect(() => {
    if (!name) return;
    api.getTeam(name).then(setTeam).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, [name]);

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
          case "run_resumed":
            logs.push(`Run resumed from phase: ${event.phase ?? "unknown"}`);
            break;
          case "phase_change":
            setPhase(event.phase);
            logs.push(`Phase: ${phaseLabels[event.phase] ?? event.phase}`);
            break;
          case "task_created": {
            const tc = event.task;
            if (!newTasks.some((nt) => nt.id === tc.id)) {
              newTasks.push({ id: tc.id, title: tc.title, status: "pending", suggestedWorker: tc.suggested_worker });
              logs.push(`Task created: ${tc.title}${tc.suggested_worker ? ` [${tc.suggested_worker}]` : ""}`);
            }
            break;
          }
          case "tasks_created":
            for (const t of event.tasks) {
              if (!newTasks.some((nt) => nt.id === t.id)) {
                newTasks.push({ id: t.id, title: t.title, status: "pending", suggestedWorker: t.suggested_worker });
              }
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
          case "cancelled":
            setPhase("cancelled");
            // Mark all non-done tasks as cancelled
            for (const t of newTasks) {
              if (t.status !== "done") t.status = "failed";
            }
            logs.push("Run was cancelled");
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
    console.log("[TeamRun] Polling effect — name:", name, "teamRunId:", teamRunId, "bgCheckRef:", bgCheckRef.current, "running:", running);
    if (!name || !teamRunId || bgCheckRef.current || running) {
      console.log("[TeamRun] Polling effect skipped — guard condition hit");
      return;
    }
    console.log("[TeamRun] Polling effect ENTERED — will fetch events");
    bgCheckRef.current = true;

    // Sync run_id to URL
    const url = new URL(window.location.href);
    if (url.searchParams.get("run_id") !== teamRunId) {
      url.searchParams.set("run_id", teamRunId);
      window.history.replaceState(null, "", url.toString());
    }

    // Reconnect via SSE — retry connecting until server is back,
    // then stream events live from the event store.
    const reconnectController = new AbortController();
    const { signal } = reconnectController;
    setPolling(true);
    setReconnecting(true);

    (async () => {
      const MAX_RETRIES = 120; // 120 × 3s = 6 minutes of retrying
      let synthContent = "";

      for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        if (signal.aborted) break;

        try {
          console.log("[TeamRun] SSE reconnect attempt", attempt);
          const stream = api.reconnectTeamStream(name!, teamRunId, signal);
          const reader = stream.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          setReconnecting(false);

          // Connected! Process events from the server.
          while (true) {
            const { done: streamDone, value } = await reader.read();
            if (streamDone) break;

            buffer += typeof value === "string" ? value : decoder.decode(value as Uint8Array, { stream: true });
            const events = parseSSE<TeamStreamEvent>(buffer);
            const lastNewline = buffer.lastIndexOf("\n");
            buffer = lastNewline >= 0 ? buffer.slice(lastNewline + 1) : buffer;

            for (const event of events) {
              switch (event.type) {
                case "phase_change":
                  setPhase(event.phase);
                  setActivityLog((prev) => [...prev, `Phase: ${phaseLabels[event.phase] ?? event.phase}`]);
                  break;
                case "task_created":
                  setTasks((prev) => {
                    if (prev.some((t) => t.id === event.task.id)) return prev;
                    return [...prev, { id: event.task.id, title: event.task.title, status: "pending", suggestedWorker: event.task.suggested_worker }];
                  });
                  setActivityLog((prev) => [...prev, `Task created: ${event.task.title}${event.task.suggested_worker ? ` [${event.task.suggested_worker}]` : ""}`]);
                  break;
                case "tasks_created":
                  setTasks((prev) => {
                    const existingIds = new Set(prev.map((t) => t.id));
                    const newOnes = event.tasks.filter((t) => !existingIds.has(t.id));
                    if (newOnes.length === 0) return prev;
                    return [...prev, ...newOnes.map((t) => ({ id: t.id, title: t.title, status: "pending", suggestedWorker: t.suggested_worker }))];
                  });
                  setActivityLog((prev) => [...prev, `${event.count} tasks created`]);
                  break;
                case "task_claimed":
                  setTasks((prev) => prev.map((t) => t.id === event.task_id ? { ...t, status: "in_progress", agent: event.agent } : t));
                  setActivityLog((prev) => [...prev, `[${event.agent}] claimed: ${event.title}`]);
                  break;
                case "task_completed":
                  setTasks((prev) => prev.map((t) => t.id === event.task_id ? { ...t, status: "done", result: event.result } : t));
                  setActivityLog((prev) => [...prev, `[${event.agent}] completed: ${event.task_id}`]);
                  break;
                case "task_failed":
                  setTasks((prev) => prev.map((t) => t.id === event.task_id ? { ...t, status: "failed", error: event.error } : t));
                  setActivityLog((prev) => [...prev, `[${event.agent}] failed: ${event.task_id} -- ${event.error}`]);
                  break;
                case "worker_start":
                  setActivityLog((prev) => [...prev, `[${event.agent}] started looking for tasks`]);
                  break;
                case "worker_done":
                  setActivityLog((prev) => [...prev, `[${event.agent}] finished -- no more tasks`]);
                  break;
                case "worker_error":
                  setActivityLog((prev) => [...prev, `Worker ${event.agent} error: ${event.error}`]);
                  break;
                case "agent_token":
                  if (event.agent === "synthesizer") {
                    synthContent += event.content;
                    setResponse(synthContent);
                  } else if (event.task_id) {
                    setTasks((prev) => prev.map((t) => t.id === event.task_id ? { ...t, streamContent: (t.streamContent ?? "") + event.content } : t));
                  }
                  break;
                case "done":
                  streamDoneRef.current = true;
                  if (event.response) setResponse(event.response);
                  setPhase("done");
                  setStats({ created: event.tasks_created, completed: event.tasks_completed, workers: event.workers_used ?? [] });
                  setActivityLog((prev) => [...prev, `Done -- ${event.tasks_completed}/${event.tasks_created} tasks completed`]);
                  break;
                case "cancelled":
                  streamDoneRef.current = true;
                  setPhase("cancelled");
                  setTasks((prev) => prev.map((t) => t.status === "done" ? t : { ...t, status: "failed" }));
                  setActivityLog((prev) => [...prev, "Run was cancelled"]);
                  break;
              }
            }
          }
          // Stream ended cleanly (server closed it) — done
          console.log("[TeamRun] SSE reconnect stream ended cleanly");
          break;
        } catch (err) {
          if (signal.aborted) break;
          // Server down or connection failed — wait and retry
          console.log("[TeamRun] SSE reconnect failed, retrying in 3s:", (err as Error)?.message ?? err);
          await new Promise((r) => setTimeout(r, 3000));
        }
      }

      setPolling(false);
      setReconnecting(false);
    })();

    return () => {
      reconnectController.abort();
    };
  }, [name, teamRunId, running, replayEvents]);

  const addLog = useCallback((msg: string) => {
    setActivityLog((prev) => [...prev, msg]);
  }, []);

  const handleNewRun = useCallback(() => {
    if (!name) return;
    // Navigate without run_id to start fresh
    window.location.href = `/ui/teams/${name}/run`;
  }, [name]);

  const handleSwitchRun = useCallback((runId: string) => {
    if (!name) return;
    window.location.href = `/ui/teams/${name}/run?run_id=${runId}`;
  }, [name]);

  const handleDeleteRun = useCallback(async (runId: string) => {
    if (!name) return;
    removeRun(userId, name, runId);
    try { await api.deleteTeamRun(name, runId); } catch { /* ignore */ }
    if (runId === teamRunId) {
      const remaining = getRuns(userId, name);
      if (remaining.length > 0) {
        window.location.href = `/ui/teams/${name}/run?run_id=${remaining[0]}`;
      } else {
        window.location.href = `/ui/teams/${name}/run`;
      }
    } else {
      // Non-current run deleted — update displayed list
      setRunList(getRuns(userId, name));
    }
  }, [name, teamRunId, userId]);

  const handleClearOldRuns = useCallback(async () => {
    if (!name) return;
    const all = getRuns(userId, name).filter((r) => r !== teamRunId);
    for (const rid of all) {
      removeRun(userId, name, rid);
      try { await api.deleteTeamRun(name, rid); } catch { /* ignore */ }
    }
    setRunList(getRuns(userId, name));
  }, [name, teamRunId, userId]);

  const handleSend = useCallback(
    async (message: string) => {
      if (!name) return;
      setRunning(true);
      streamDoneRef.current = false;
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

      // Watchdog: if no data received for 30s, abort the stream.
      // This handles the case where the server crashes and the TCP
      // connection hangs without closing cleanly.
      let lastDataTime = Date.now();
      const watchdog = setInterval(() => {
        if (Date.now() - lastDataTime > 30_000) {
          console.log("[TeamRun] Watchdog: no data for 30s — aborting stream");
          controller.abort();
          clearInterval(watchdog);
        }
      }, 5_000);

      try {
        const stream = api.runTeamStream(name, { message }, controller.signal);
        const reader = stream.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let synthContent = "";

        while (true) {
          const { done: streamDone, value } = await reader.read();
          lastDataTime = Date.now();
          if (streamDone) break;

          buffer += typeof value === "string" ? value : decoder.decode(value as Uint8Array, { stream: true });
          const events = parseSSE<TeamStreamEvent>(buffer);
          const lastNewline = buffer.lastIndexOf("\n");
          buffer = lastNewline >= 0 ? buffer.slice(lastNewline + 1) : buffer;

          for (const event of events) {
            switch (event.type) {
              case "team_start":
                setTeamRunId(event.team_run_id);
                addRun(userId, name, event.team_run_id);
                setRunList(getRuns(userId, name));
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
              case "task_created":
                setTasks((prev) => {
                  if (prev.some((t) => t.id === event.task.id)) return prev;
                  return [...prev, { id: event.task.id, title: event.task.title, status: "pending", suggestedWorker: event.task.suggested_worker }];
                });
                addLog(`Task created: ${event.task.title}${event.task.suggested_worker ? ` [${event.task.suggested_worker}]` : ""}`);
                break;
              case "tasks_created":
                setTasks((prev) => {
                  const existingIds = new Set(prev.map((t) => t.id));
                  const newOnes = event.tasks.filter((t) => !existingIds.has(t.id));
                  if (newOnes.length === 0) return prev;
                  return [...prev, ...newOnes.map((t) => ({ id: t.id, title: t.title, status: "pending", suggestedWorker: t.suggested_worker }))];
                });
                addLog(`${event.count} tasks created`);
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
                streamDoneRef.current = true;
                if (event.response) setResponse(event.response);
                setPhase("done");
                setStats({
                  created: event.tasks_created,
                  completed: event.tasks_completed,
                  workers: event.workers_used,
                });
                addLog(`Done -- ${event.tasks_completed}/${event.tasks_created} tasks completed`);
                break;
              case "cancelled":
                streamDoneRef.current = true;
                setPhase("cancelled");
                setTasks((prev) => prev.map((t) => t.status === "done" ? t : { ...t, status: "failed" }));
                addLog("Run was cancelled");
                break;
            }
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          console.log("[TeamRun] Stream aborted (user or watchdog)");
        } else {
          console.log("[TeamRun] Stream error:", err);
          addLog(`Error: ${err instanceof Error ? err.message : "Request failed"}`);
        }
      } finally {
        clearInterval(watchdog);
        console.log("[TeamRun] Stream finally — streamDone:", streamDoneRef.current, "teamRunId:", teamRunId, "bgCheckRef:", bgCheckRef.current);
        abortRef.current = null;

        // If the stream ended without a "done" event (e.g. server crash/restart),
        // reset bgCheckRef so the polling useEffect re-runs when running→false.
        if (!streamDoneRef.current) {
          console.log("[TeamRun] Stream dropped without done — resetting bgCheckRef for polling");
          bgCheckRef.current = false;
        }

        setRunning(false);
      }
    },
    [name, addLog],
  );

  const handleRetryTask = useCallback(
    async (taskId: string) => {
      if (!name || !teamRunId) return;
      addLog(`Retrying task ${taskId}...`);
      // Reset task in local state to in_progress
      setTasks((prev) =>
        prev.map((t) =>
          t.id === taskId ? { ...t, status: "in_progress", error: undefined, result: undefined, streamContent: "" } : t,
        ),
      );

      try {
        const stream = api.retryTeamTask(name, teamRunId, taskId);
        const reader = stream.getReader();

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const event of parseSSE<TeamStreamEvent>(value)) {
            switch (event.type) {
              case "task_retry":
                addLog(`[${event.agent}] retrying: ${event.title}`);
                break;
              case "agent_token":
                setTasks((prev) =>
                  prev.map((t) =>
                    t.id === event.task_id ? { ...t, streamContent: (t.streamContent ?? "") + event.content } : t,
                  ),
                );
                break;
              case "agent_tool":
                addLog(`[${event.agent}] tool: ${event.name}`);
                break;
              case "task_completed":
                setTasks((prev) =>
                  prev.map((t) =>
                    t.id === event.task_id ? { ...t, status: "done", result: event.result, streamContent: undefined } : t,
                  ),
                );
                addLog(`[${event.agent}] completed: ${event.task_id}`);
                break;
              case "task_failed":
                setTasks((prev) =>
                  prev.map((t) =>
                    t.id === event.task_id ? { ...t, status: "failed", error: event.error, streamContent: undefined } : t,
                  ),
                );
                addLog(`[${event.agent}] retry failed: ${event.task_id} -- ${event.error}`);
                break;
              case "retry_done":
                addLog(`Retry complete for task ${event.task_id}`);
                break;
              case "error":
                addLog(`Retry error: ${event.detail}`);
                break;
            }
          }
        }
      } catch (err) {
        addLog(`Retry error: ${err instanceof Error ? err.message : "Request failed"}`);
      }
    },
    [name, teamRunId, addLog],
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
                <span className="font-mono">{teamRunId.slice(0, 8)}...</span>
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
            <span>|</span>
            <button
              className="hover:text-gray-700 dark:hover:text-gray-300"
              onClick={handleNewRun}
            >
              + new run
            </button>
            {runList.length > 1 && (
              <>
                <span>|</span>
                {runList.filter((r) => r !== teamRunId).map((r) => (
                  <span key={r} className="inline-flex items-center gap-0.5">
                    <button
                      className="font-mono hover:text-indigo-500"
                      onClick={() => handleSwitchRun(r)}
                    >
                      {r.slice(0, 6)}
                    </button>
                    <button
                      className="text-red-400 hover:text-red-600"
                      onClick={() => handleDeleteRun(r)}
                    >
                      x
                    </button>
                  </span>
                ))}
                <span>|</span>
                <button
                  className="text-red-400 hover:text-red-600"
                  onClick={handleClearOldRuns}
                >
                  clear old
                </button>
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
              <span className={`inline-block w-1.5 h-1.5 rounded-full ${reconnecting ? "bg-yellow-500" : "bg-indigo-500"} animate-pulse`} />
              {reconnecting ? "Connection lost \u2014 reconnecting..." : "Team is working in the background..."}
            </div>
          )}
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} onRetry={!running ? handleRetryTask : undefined} />
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
        <div className="flex items-center gap-2">
          <div className="flex-1">
            <ChatInput onSend={handleSend} disabled={running || polling} />
          </div>
          {(running || polling) && teamRunId && (
            <button
              onClick={async () => {
                abortRef.current?.abort();
                try { await api.cancelTeamRun(name!, teamRunId); } catch { /* ignore */ }
                setRunning(false);
                setPolling(false);
              }}
              className="shrink-0 rounded border border-red-300 dark:border-red-700 px-3 py-1.5 text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30"
            >
              Cancel
            </button>
          )}
        </div>
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
