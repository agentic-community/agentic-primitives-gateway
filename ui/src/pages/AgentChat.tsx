import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { StreamEvent } from "../api/types";
import ChatInput from "../components/ChatInput";
import ChatMessage from "../components/ChatMessage";
import LoadingSpinner from "../components/LoadingSpinner";
import MemoryPanel from "../components/MemoryPanel";
import ToolCallBlock from "../components/ToolCallBlock";
import ToolsPanel from "../components/ToolsPanel";
import { useAgent } from "../hooks/useAgent";

interface Turn {
  userMessage: string;
  assistantContent: string;
  toolsCalled: string[];
  turnsUsed: number;
  done: boolean;
  error?: string;
}

function generateSessionId() {
  return crypto.randomUUID();
}

function parseSSE(chunk: string): StreamEvent[] {
  const events: StreamEvent[] = [];
  const lines = chunk.split("\n");
  for (const line of lines) {
    if (line.startsWith("data: ")) {
      try {
        events.push(JSON.parse(line.slice(6)));
      } catch {
        // skip malformed lines
      }
    }
  }
  return events;
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
      setTurns((prev) => [
        ...prev,
        { userMessage: message, assistantContent: "", toolsCalled: [], turnsUsed: 0, done: false },
      ]);
      setSending(true);

      try {
        const stream = api.chatStream(name, {
          message,
          session_id: sessionId,
        });
        const reader = stream.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += typeof value === "string" ? value : decoder.decode(value as Uint8Array, { stream: true });

          // Process complete SSE lines
          const events = parseSSE(buffer);
          // Keep any incomplete line in the buffer
          const lastNewline = buffer.lastIndexOf("\n");
          buffer = lastNewline >= 0 ? buffer.slice(lastNewline + 1) : buffer;

          for (const event of events) {
            if (event.type === "token") {
              setTurns((prev) => {
                const copy = [...prev];
                const turn = { ...copy[turnIndex] };
                turn.assistantContent += event.content;
                copy[turnIndex] = turn;
                return copy;
              });
            } else if (event.type === "tool_call_start") {
              setTurns((prev) => {
                const copy = [...prev];
                const turn = { ...copy[turnIndex] };
                turn.toolsCalled = [...turn.toolsCalled, event.name];
                copy[turnIndex] = turn;
                return copy;
              });
            } else if (event.type === "done") {
              setTurns((prev) => {
                const copy = [...prev];
                copy[turnIndex] = {
                  ...copy[turnIndex],
                  assistantContent: event.response,
                  toolsCalled: event.tools_called,
                  turnsUsed: event.turns_used,
                  done: true,
                };
                return copy;
              });
              // Refresh memory panel if memory tools were called
              if (
                event.tools_called.some((t: string) =>
                  ["remember", "forget", "recall", "search_memory", "list_memories"].includes(t),
                )
              ) {
                setMemoryRefreshKey((k) => k + 1);
              }
            }
          }
        }
      } catch (err) {
        setTurns((prev) => {
          const copy = [...prev];
          copy[turnIndex] = {
            ...copy[turnIndex],
            error: err instanceof Error ? err.message : "Request failed",
            done: true,
          };
          return copy;
        });
      } finally {
        setSending(false);
      }
    },
    [name, sessionId, turns.length],
  );

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
            <span
              className="font-mono cursor-pointer hover:text-gray-700 dark:hover:text-gray-300"
              title="Click to copy session ID"
              onClick={() => navigator.clipboard.writeText(sessionId)}
            >
              session: {sessionId.slice(0, 8)}...
            </span>
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
            {turn.assistantContent ? (
              <ChatMessage role="assistant" content={turn.assistantContent} />
            ) : turn.error ? (
              <div className="rounded-lg bg-red-50 dark:bg-red-950/40 px-4 py-3 text-sm text-red-600 dark:text-red-400 mr-12">
                Error: {turn.error}
              </div>
            ) : !turn.done ? (
              <div className="mr-12 rounded-lg bg-gray-50 dark:bg-gray-900 px-4 py-3">
                <div className="flex items-center gap-2 text-xs text-gray-400 dark:text-gray-500">
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse" />
                  Thinking...
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
