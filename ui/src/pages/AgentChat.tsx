import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { StreamArtifact, StreamEvent } from "../api/types";
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

function generateSessionId() {
  return crypto.randomUUID();
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
  const [sessionId] = useState(generateSessionId);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sending, setSending] = useState(false);
  const [memoryRefreshKey, setMemoryRefreshKey] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [turns, sending]);

  const handleSend = useCallback(
    async (message: string) => {
      if (!name) return;
      const turnIndex = turns.length;
      const emptyTurn: Turn = {
        userMessage: message, assistantContent: "", toolsCalled: [],
        turnsUsed: 0, done: false, subAgents: [], artifacts: [],
      };
      setTurns((prev) => [...prev, emptyTurn]);
      setSending(true);

      try {
        const stream = api.chatStream(name, { message, session_id: sessionId });
        const reader = stream.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += typeof value === "string" ? value : decoder.decode(value as Uint8Array, { stream: true });
          const events = parseSSE(buffer);
          const lastNewline = buffer.lastIndexOf("\n");
          buffer = lastNewline >= 0 ? buffer.slice(lastNewline + 1) : buffer;

          for (const event of events) {
            handleStreamEvent(event, turnIndex);
          }
        }
      } catch (err) {
        setTurns((prev) =>
          updateTurn(prev, turnIndex, (t) => ({
            ...t,
            error: err instanceof Error ? err.message : "Request failed",
            done: true,
          })),
        );
      } finally {
        setSending(false);
      }
    },
    [name, sessionId, turns.length],
  );

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
          // Static delegation: call_researcher → mark "researcher" done
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
            <button
              className="font-mono hover:text-gray-700 dark:hover:text-gray-300"
              aria-label="Copy session ID"
              onClick={() => navigator.clipboard.writeText(sessionId)}
            >
              session: {sessionId.slice(0, 8)}...
            </button>
            <span>|</span>
            <span>turns: {turnsUsed}</span>
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
        {turns.length === 0 && (
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
      </div>

      {/* Input */}
      <div className="border-t border-gray-200 dark:border-gray-800 pt-3">
        <ChatInput onSend={handleSend} disabled={sending} />
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
