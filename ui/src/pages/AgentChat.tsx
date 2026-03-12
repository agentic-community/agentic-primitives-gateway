import { useCallback, useEffect, useRef, useState } from "react";
import { useAutoScroll } from "../hooks/useAutoScroll";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { StreamArtifact, StreamEvent } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import ArtifactBlock from "../components/ArtifactBlock";
import ChatInput from "../components/ChatInput";
import ChatMessage from "../components/ChatMessage";
import LoadingSpinner from "../components/LoadingSpinner";
import MemoryPanel from "../components/MemoryPanel";
import SubAgentBlock from "../components/SubAgentBlock";
import type { SubAgentActivity } from "../components/SubAgentBlock";
import ToolCallBlock from "../components/ToolCallBlock";
import ToolsPanel from "../components/ToolsPanel";
import { useAgent } from "../hooks/useAgent";
import { parseSSE } from "../lib/sse";

interface Turn {
  userMessage: string;
  assistantContent: string;
  toolsCalled: string[];
  turnsUsed: number;
  done: boolean;
  subAgents: SubAgentActivity[];
  artifacts: StreamArtifact[];
  error?: string;
}

const MEMORY_TOOLS = new Set(["remember", "forget", "recall", "search_memory", "list_memories"]);

function sessionsKey(userId: string, agentName: string) {
  return `agent-sessions:${userId}:${agentName}`;
}

function getSessions(userId: string, agentName: string): string[] {
  try {
    return JSON.parse(localStorage.getItem(sessionsKey(userId, agentName)) || "[]");
  } catch {
    return [];
  }
}

function saveSessions(userId: string, agentName: string, sessions: string[]) {
  localStorage.setItem(sessionsKey(userId, agentName), JSON.stringify(sessions));
}

function getOrCreateSessionId(userId: string, agentName: string): [string, boolean] {
  const sessions = getSessions(userId, agentName);
  if (sessions.length > 0) return [sessions[0], true];
  const id = crypto.randomUUID();
  saveSessions(userId, agentName, [id]);
  return [id, false];
}

function addSession(userId: string, agentName: string, sessionId: string) {
  const sessions = getSessions(userId, agentName);
  if (!sessions.includes(sessionId)) {
    saveSessions(userId, agentName, [sessionId, ...sessions]);
  }
}

function removeSession(userId: string, agentName: string, sessionId: string) {
  const sessions = getSessions(userId, agentName).filter((s) => s !== sessionId);
  saveSessions(userId, agentName, sessions);
}

/** Update a specific turn in the turns array by index. */
function updateTurn(
  prev: Turn[],
  index: number,
  updater: (turn: Turn) => Turn,
): Turn[] {
  const copy = [...prev];
  copy[index] = updater({ ...copy[index] });
  return copy;
}

export default function AgentChat() {
  const { name } = useParams<{ name: string }>();
  const { agent, loading, error } = useAgent(name!);
  const { user } = useAuth();
  const userId = user?.profile?.sub || "anonymous";

  // Session ID: from URL param > localStorage > generate new.
  // isReturningSession tracks whether this is a returning visit (existing session).
  const [sessionState] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("session_id");
    if (fromUrl) return { id: fromUrl, returning: true };
    const uid = user?.profile?.sub || "anonymous";
    const [id, returning] = getOrCreateSessionId(uid, name!);
    return { id, returning };
  });
  const sessionId = sessionState.id;
  const isReturningSession = sessionState.returning;

  const [turns, setTurns] = useState<Turn[]>([]);
  const [sending, setSending] = useState(false);
  const [memoryRefreshKey, setMemoryRefreshKey] = useState(0);
  const historyLoadedRef = useRef(false);
  const streamDoneRef = useRef(false);
  const [polling, setPolling] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [sessionList, setSessionList] = useState<string[]>(() => getSessions(userId, name!));
  const scrollRef = useAutoScroll([turns, sending]);
  const abortRef = useRef<AbortController | null>(null);

  // Sync session_id into URL and localStorage (once on mount)
  useEffect(() => {
    if (!name) return;
    addSession(userId, name, sessionId);
    setSessionList(getSessions(userId, name));
    const url = new URL(window.location.href);
    if (url.searchParams.get("session_id") !== sessionId) {
      url.searchParams.set("session_id", sessionId);
      window.history.replaceState(null, "", url.toString());
    }
  }, [name, sessionId]);

  // Abort stream on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // Load conversation history and poll for in-progress background runs.
  useEffect(() => {
    if (!name || historyLoadedRef.current) return;
    historyLoadedRef.current = true;

    const POLL_INTERVAL = 3000;
    const MAX_POLLS = 60;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    function restoreTurns(messages: { role: string; content: string }[]) {
      const restored: Turn[] = [];
      for (let i = 0; i < messages.length; i += 2) {
        const userMsg = messages[i];
        const assistantMsg = messages[i + 1];
        if (userMsg?.role === "user" && assistantMsg?.role === "assistant") {
          restored.push({
            userMessage: userMsg.content,
            assistantContent: assistantMsg.content,
            toolsCalled: [], turnsUsed: 1, done: true,
            subAgents: [], artifacts: [],
          });
        }
      }
      return restored.reverse();
    }

    async function poll(attempt: number) {
      if (attempt >= MAX_POLLS) {
        setPolling(false);
        return;
      }
      try {
        const [history, status] = await Promise.all([
          api.getSessionHistory(name!, sessionId),
          api.getSessionStatus(name!, sessionId),
        ]);
        if (history.messages && history.messages.length > 0) {
          const restored = restoreTurns(history.messages);
          if (restored.length > 0) setTurns(restored);
        }
        if (status.status === "running") {
          setPolling(true);
          if (!stopped) timer = setTimeout(() => poll(attempt + 1), POLL_INTERVAL);
          return;
        }
        // Status is idle — run completed
        setPolling(false);
        return;
      } catch {
        // Server might be down (restarting). Keep polling until MAX_POLLS.
        setPolling(true);
        if (!stopped) timer = setTimeout(() => poll(attempt + 1), POLL_INTERVAL);
      }
    }

    // First load: get history, and if returning, check for active run
    api.getSessionHistory(name!, sessionId).then((history) => {
      if (history.messages && history.messages.length > 0) {
        const restored = restoreTurns(history.messages);
        if (restored.length > 0) setTurns(restored);
      }
      // If returning session, check for active background run
      if (isReturningSession) {
        api.getSessionStatus(name!, sessionId).then((status) => {
          if (status.status === "running") {
            setPolling(true);
            if (!stopped) timer = setTimeout(() => poll(0), POLL_INTERVAL);
          }
        }).catch(() => {});
      }
    }).catch(() => {});

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [name, sessionId, isReturningSession]);

  const handleSend = useCallback(
    async (message: string) => {
      if (!name) return;
      const turnIndex = turns.length;
      const emptyTurn: Turn = {
        userMessage: message, assistantContent: "", toolsCalled: [],
        turnsUsed: 0, done: false, subAgents: [], artifacts: [],
      };
      setTurns((prev) => [...prev, emptyTurn]);
      streamDoneRef.current = false;
      setSending(true);

      const controller = new AbortController();
      abortRef.current = controller;

      // Watchdog: abort if no data for 30s (server crash detection)
      let lastDataTime = Date.now();
      const watchdog = setInterval(() => {
        if (Date.now() - lastDataTime > 30_000) {
          console.log("[AgentChat] Watchdog: no data for 30s — aborting stream");
          controller.abort();
          clearInterval(watchdog);
        }
      }, 5_000);

      try {
        const stream = api.chatStream(name, { message, session_id: sessionId }, controller.signal);
        const reader = stream.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          lastDataTime = Date.now();
          if (done) break;

          buffer += typeof value === "string" ? value : decoder.decode(value as Uint8Array, { stream: true });
          const events = parseSSE<StreamEvent>(buffer);
          const lastNewline = buffer.lastIndexOf("\n");
          buffer = lastNewline >= 0 ? buffer.slice(lastNewline + 1) : buffer;

          for (const event of events) {
            handleStreamEvent(event, turnIndex);
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          console.log("[AgentChat] Stream aborted (user or watchdog)");
        } else {
          setTurns((prev) =>
            updateTurn(prev, turnIndex, (t) => ({
              ...t,
              error: err instanceof Error ? err.message : "Request failed",
              done: true,
            })),
          );
        }
      } finally {
        clearInterval(watchdog);
        abortRef.current = null;
        setSending(false);

        // If the stream ended without a "done" event (e.g. server crash/restart),
        // reconnect via SSE to get events from the event store.
        if (!streamDoneRef.current && name && sessionId) {
          setPolling(true);
          setReconnecting(true);
          const reconnectCtrl = new AbortController();

          (async () => {
            for (let attempt = 0; attempt < 120; attempt++) {
              if (reconnectCtrl.signal.aborted) break;
              try {
                console.log("[AgentChat] SSE reconnect attempt", attempt);
                const stream = api.reconnectSessionStream(name, sessionId, reconnectCtrl.signal);
                const reader = stream.getReader();
                const decoder = new TextDecoder();
                let buf = "";
                setReconnecting(false);

                while (true) {
                  const { done: d, value: v } = await reader.read();
                  if (d) break;
                  buf += typeof v === "string" ? v : decoder.decode(v as Uint8Array, { stream: true });
                  const evts = parseSSE<StreamEvent>(buf);
                  const nl = buf.lastIndexOf("\n");
                  buf = nl >= 0 ? buf.slice(nl + 1) : buf;
                  for (const evt of evts) {
                    handleStreamEvent(evt, turnIndex);
                  }
                }
                console.log("[AgentChat] SSE reconnect stream ended");
                break;
              } catch (err) {
                if (reconnectCtrl.signal.aborted) break;
                console.log("[AgentChat] SSE reconnect failed, retrying:", (err as Error)?.message);
                await new Promise((r) => setTimeout(r, 3000));
              }
            }
            setPolling(false);
            setReconnecting(false);

            // Reload full history after reconnect completes
            try {
              const history = await api.getSessionHistory(name, sessionId);
              if (history.messages?.length) {
                const restored: Turn[] = [];
                for (let i = 0; i < history.messages.length; i += 2) {
                  const u = history.messages[i];
                  const a = history.messages[i + 1];
                  if (u?.role === "user" && a?.role === "assistant") {
                    restored.push({ userMessage: u.content, assistantContent: a.content, toolsCalled: [], turnsUsed: 1, done: true, subAgents: [], artifacts: [] });
                  }
                }
                if (restored.length) setTurns(restored.reverse());
              }
            } catch { /* ignore */ }
          })();
        }
      }
    },
    [name, sessionId, turns.length],
  );

  const handleNewSession = useCallback(() => {
    if (!name) return;
    const newId = crypto.randomUUID();
    addSession(userId, name, newId);
    window.location.href = `/ui/agents/${name}/chat?session_id=${newId}`;
  }, [name, userId]);

  const handleSwitchSession = useCallback((sid: string) => {
    if (!name) return;
    window.location.href = `/ui/agents/${name}/chat?session_id=${sid}`;
  }, [name]);

  const handleDeleteSession = useCallback(async (sid: string) => {
    if (!name) return;
    removeSession(userId, name, sid);
    try { await api.deleteSession(name, sid); } catch { /* ignore */ }
    // If deleting current session, switch to another or create new
    if (sid === sessionId) {
      const remaining = getSessions(userId, name);
      if (remaining.length > 0) {
        window.location.href = `/ui/agents/${name}/chat?session_id=${remaining[0]}`;
      } else {
        const newId = crypto.randomUUID();
        addSession(userId, name, newId);
        window.location.href = `/ui/agents/${name}/chat?session_id=${newId}`;
      }
    } else {
      // Non-current session deleted — update displayed list
      setSessionList(getSessions(userId, name));
    }
  }, [name, sessionId, userId]);

  const handleClearOldSessions = useCallback(async () => {
    if (!name) return;
    const all = getSessions(userId, name).filter((s) => s !== sessionId);
    for (const sid of all) {
      removeSession(userId, name, sid);
      try { await api.deleteSession(name, sid); } catch { /* ignore */ }
    }
    setSessionList(getSessions(userId, name));
  }, [name, sessionId, userId]);

  function handleStreamEvent(event: StreamEvent, turnIndex: number) {
    switch (event.type) {
      case "token":
        setTurns((prev) =>
          updateTurn(prev, turnIndex, (t) => ({
            ...t,
            assistantContent: t.assistantContent + event.content,
          })),
        );
        break;

      case "tool_call_start":
        setTurns((prev) =>
          updateTurn(prev, turnIndex, (t) => ({
            ...t,
            toolsCalled: [...t.toolsCalled, event.name],
          })),
        );
        break;

      case "sub_agent_token":
        setTurns((prev) =>
          updateTurn(prev, turnIndex, (t) => ({
            ...t,
            subAgents: upsertSubAgent(t.subAgents, event.agent, (sa) => ({
              ...sa,
              content: sa.content + event.content,
              status: "streaming",
            })),
          })),
        );
        break;

      case "sub_agent_tool":
        setTurns((prev) =>
          updateTurn(prev, turnIndex, (t) => ({
            ...t,
            subAgents: upsertSubAgent(t.subAgents, event.agent, (sa) => ({
              ...sa,
              status: `using ${event.name}`,
            })),
          })),
        );
        break;

      case "tool_call_result":
        if (event.name.startsWith("call_")) {
          // Static delegation: call_researcher -> mark "researcher" done
          const agentName = event.name.replace("call_", "");
          setTurns((prev) =>
            updateTurn(prev, turnIndex, (t) => ({
              ...t,
              subAgents: t.subAgents.map((s) =>
                s.agent === agentName ? { ...s, status: "done" } : s,
              ),
            })),
          );
        } else if (event.name === "delegate_to") {
          // Dynamic delegation: mark any streaming sub-agents as done
          setTurns((prev) =>
            updateTurn(prev, turnIndex, (t) => ({
              ...t,
              subAgents: t.subAgents.map((s) =>
                s.status !== "done" ? { ...s, status: "done" } : s,
              ),
            })),
          );
        }
        break;

      case "done":
        streamDoneRef.current = true;
        setTurns((prev) =>
          updateTurn(prev, turnIndex, (t) => ({
            ...t,
            assistantContent: event.response,
            toolsCalled: event.tools_called,
            turnsUsed: event.turns_used,
            artifacts: (event.artifacts ?? []).filter((a: StreamArtifact) => a.code || a.output),
            done: true,
          })),
        );
        if (event.tools_called.some((t: string) => MEMORY_TOOLS.has(t))) {
          setMemoryRefreshKey((k) => k + 1);
        }
        break;
    }
  }

  if (loading) return <LoadingSpinner className="mt-32" />;
  if (error || !agent) {
    return (
      <div className="mt-32 text-center text-sm text-red-600 dark:text-red-400">
        {error ?? "Agent not found"}
      </div>
    );
  }

  const turnsUsed = turns
    .filter((t) => t.done)
    .reduce((sum, t) => sum + t.turnsUsed, 0);

  return (
    <div className="flex h-full flex-col max-w-3xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-800 pb-3 mb-4">
        <div>
          <div className="flex items-center gap-2">
            <Link
              to="/agents"
              className="text-xs text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
            >
              Agents /
            </Link>
            <h1 className="text-sm font-semibold font-mono text-gray-900 dark:text-gray-100">
              {agent.name}
            </h1>
          </div>
          <div className="flex items-center gap-3 mt-1 text-[11px] text-gray-500 dark:text-gray-400">
            <span className="font-mono">{agent.model}</span>
            <span>|</span>
            <span className="font-mono">{sessionId.slice(0, 8)}...</span>
            <span>|</span>
            <span>turns: {turnsUsed}</span>
            <span>|</span>
            <button
              className="hover:text-gray-700 dark:hover:text-gray-300"
              onClick={handleNewSession}
            >
              + new
            </button>
            {sessionList.length > 1 && (
              <>
                <span>|</span>
                {sessionList.filter((s) => s !== sessionId).map((s) => (
                  <span key={s} className="inline-flex items-center gap-0.5">
                    <button
                      className="font-mono hover:text-indigo-500"
                      onClick={() => handleSwitchSession(s)}
                    >
                      {s.slice(0, 6)}
                    </button>
                    <button
                      className="text-red-400 hover:text-red-600"
                      onClick={() => handleDeleteSession(s)}
                    >
                      x
                    </button>
                  </span>
                ))}
                <span>|</span>
                <button
                  className="text-red-400 hover:text-red-600"
                  onClick={handleClearOldSessions}
                >
                  clear old
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Tools & Memory panels */}
      <div className="mb-3 space-y-2">
        <ToolsPanel agentName={agent.name} />
        {agent.primitives?.memory?.enabled && (
          <MemoryPanel
            agentName={agent.name}
            sessionId={sessionId}
            refreshKey={memoryRefreshKey}
          />
        )}
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-3 pb-4">
        {turns.length === 0 && !polling && (
          <p className="text-center text-sm text-gray-400 dark:text-gray-500 py-16">
            Start a conversation with {agent.name}
          </p>
        )}
        {turns.map((turn, i) => (
          <div key={i} className="space-y-2">
            <ChatMessage role="user" content={turn.userMessage} />
            {turn.toolsCalled.length > 0 && (
              <ToolCallBlock tools={turn.toolsCalled} />
            )}
            {turn.subAgents.length > 0 && (
              <div className="mr-12 space-y-1.5">
                {turn.subAgents.map((sa) => (
                  <SubAgentBlock key={sa.agent} activity={sa} />
                ))}
              </div>
            )}
            {turn.assistantContent ? (
              <>
                <ChatMessage role="assistant" content={turn.assistantContent} />
                {turn.artifacts.length > 0 && (
                  <div className="mr-12 space-y-1.5">
                    {turn.artifacts.map((a, idx) => (
                      <ArtifactBlock key={idx} artifact={a} />
                    ))}
                  </div>
                )}
              </>
            ) : turn.error ? (
              <div className="rounded-lg bg-red-50 dark:bg-red-950/40 px-4 py-3 text-sm text-red-600 dark:text-red-400 mr-12">
                Error: {turn.error}
              </div>
            ) : !turn.done ? (
              <div className="mr-12 rounded-lg bg-gray-50 dark:bg-gray-900 px-4 py-3">
                <div className="flex items-center gap-2 text-xs text-gray-400 dark:text-gray-500">
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse" />
                  {turn.subAgents.length > 0 ? `Working with ${turn.subAgents.map(s => s.agent).join(", ")}...` : "Thinking..."}
                </div>
              </div>
            ) : null}
          </div>
        ))}
        {polling && (
          <div className="mr-12 rounded-lg bg-gray-50 dark:bg-gray-900 px-4 py-3">
            <div className="flex items-center gap-2 text-xs text-gray-400 dark:text-gray-500">
              <span className={`inline-block w-1.5 h-1.5 rounded-full ${reconnecting ? "bg-yellow-500" : "bg-indigo-500"} animate-pulse`} />
              {reconnecting ? "Connection lost \u2014 reconnecting..." : "Agent is working in the background..."}
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
        <div className="flex items-center gap-2">
          <div className="flex-1">
            <ChatInput onSend={handleSend} disabled={sending || polling} />
          </div>
          {(sending || polling) && (
            <button
              onClick={async () => {
                abortRef.current?.abort();
                try { await api.cancelSessionRun(name!, sessionId); } catch { /* ignore */ }
                setSending(false);
                setPolling(false);
              }}
              className="shrink-0 rounded border border-red-300 dark:border-red-700 px-3 py-1.5 text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30"
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** Upsert a sub-agent entry in the array, creating if not found. */
function upsertSubAgent(
  agents: SubAgentActivity[],
  name: string,
  updater: (existing: SubAgentActivity) => SubAgentActivity,
): SubAgentActivity[] {
  const idx = agents.findIndex((s) => s.agent === name);
  if (idx >= 0) {
    return agents.map((s, i) => (i === idx ? updater(s) : s));
  }
  return [...agents, updater({ agent: name, status: "", content: "" })];
}
