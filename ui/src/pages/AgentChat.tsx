import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { api } from "../api/client";
import type { StreamArtifact, StreamEvent } from "../api/types";
import ChatInput from "../components/ChatInput";
import ChatMessage from "../components/ChatMessage";
import LoadingSpinner from "../components/LoadingSpinner";
import MemoryPanel from "../components/MemoryPanel";
import ToolCallBlock from "../components/ToolCallBlock";
import ToolsPanel from "../components/ToolsPanel";
import { useAgent } from "../hooks/useAgent";

interface SubAgentActivity {
  agent: string;
  status: string;
  content: string;
}

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

const codeTheme: Record<string, React.CSSProperties> = {
  'pre[class*="language-"]': { background: "transparent", margin: 0, padding: 0, fontSize: "0.75rem", lineHeight: "1.5" },
  'code[class*="language-"]': { background: "transparent", fontSize: "0.75rem" },
  comment: { color: "#6b7280" }, string: { color: "#059669" }, keyword: { color: "#7c3aed" },
  number: { color: "#d97706" }, function: { color: "#2563eb" }, operator: { color: "#6b7280" },
  punctuation: { color: "#6b7280" }, "class-name": { color: "#0891b2" }, builtin: { color: "#0891b2" },
};

function ArtifactBlock({ artifact }: { artifact: StreamArtifact }) {
  const [open, setOpen] = useState(false);
  const label = artifact.tool_name.startsWith("call_")
    ? `${artifact.tool_name.replace("call_", "")} output`
    : artifact.tool_name;

  return (
    <div className="rounded-lg border border-indigo-200 dark:border-indigo-900/50 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-[11px] text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50/50 dark:hover:bg-indigo-950/30 transition-colors"
      >
        <span className={`transition-transform text-[10px] ${open ? "rotate-90" : ""}`}>&#9654;</span>
        <span className="font-medium">{label}</span>
        {artifact.code && <span className="text-indigo-400 dark:text-indigo-500 font-mono">{artifact.language}</span>}
      </button>
      {open && (
        <div className="border-t border-indigo-200 dark:border-indigo-900/50">
          {artifact.code && (
            <div className="max-h-96 overflow-auto">
              <SyntaxHighlighter
                style={codeTheme}
                language={artifact.language || "python"}
                PreTag="div"
                className="bg-gray-50 dark:bg-gray-900 p-3 text-xs"
              >
                {artifact.code}
              </SyntaxHighlighter>
            </div>
          )}
          {artifact.output && (
            <div className="border-t border-indigo-100 dark:border-indigo-900/30 bg-gray-50 dark:bg-gray-900 px-3 py-2">
              <p className="text-[10px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500 mb-1">Output</p>
              <pre className="text-[11px] text-gray-600 dark:text-gray-400 whitespace-pre-wrap max-h-48 overflow-auto">{artifact.output}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SubAgentBlock({ activity }: { activity: SubAgentActivity }) {
  const isDone = activity.status === "done";
  // Expanded by default while streaming, collapsed by default once done
  const [open, setOpen] = useState(!isDone);

  // Auto-expand while still streaming
  useEffect(() => {
    if (!isDone) setOpen(true);
  }, [isDone]);

  return (
    <div
      className={`rounded-lg border transition-colors duration-300 overflow-hidden ${
        isDone
          ? "border-green-200 dark:border-green-900/50 bg-green-50/50 dark:bg-green-950/20"
          : "border-rose-200 dark:border-rose-900/50 bg-rose-50/50 dark:bg-rose-950/20"
      }`}
    >
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-1.5 hover:bg-black/5 dark:hover:bg-white/5 transition-colors"
      >
        <span className={`transition-transform text-[10px] ${isDone ? "text-green-500" : "text-rose-500"} ${open ? "rotate-90" : ""}`}>
          &#9654;
        </span>
        {isDone ? (
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-green-500" />
        ) : (
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-rose-500 animate-pulse" />
        )}
        <span className={`text-[11px] font-mono font-medium ${isDone ? "text-green-600 dark:text-green-400" : "text-rose-600 dark:text-rose-400"}`}>
          {activity.agent}
        </span>
        {!isDone && (
          <span className="text-[11px] text-rose-400 dark:text-rose-500">{activity.status}</span>
        )}
        {isDone && (
          <span className="text-[11px] text-green-500 dark:text-green-500">done</span>
        )}
      </button>
      {open && activity.content && (
        <div className={`border-t px-3 py-2 ${isDone ? "border-green-200 dark:border-green-900/50" : "border-rose-200 dark:border-rose-900/50"}`}>
          <div className="text-[11px] text-gray-600 dark:text-gray-400 max-h-64 overflow-y-auto overflow-x-hidden break-words prose prose-xs dark:prose-invert max-w-none prose-p:my-0.5 prose-pre:my-1 prose-ul:my-0.5 prose-ol:my-0.5 prose-li:my-0 prose-headings:my-1 prose-code:text-[11px]">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {activity.content}
            </ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
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
        { userMessage: message, assistantContent: "", toolsCalled: [], turnsUsed: 0, done: false, subAgents: [], artifacts: [] },
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
            } else if (event.type === "sub_agent_token") {
              setTurns((prev) => {
                const copy = [...prev];
                const turn = { ...copy[turnIndex] };
                const existing = turn.subAgents.find((s) => s.agent === event.agent);
                if (existing) {
                  turn.subAgents = turn.subAgents.map((s) =>
                    s.agent === event.agent
                      ? { ...s, content: s.content + event.content, status: "streaming" }
                      : s,
                  );
                } else {
                  turn.subAgents = [...turn.subAgents, { agent: event.agent, status: "streaming", content: event.content }];
                }
                copy[turnIndex] = turn;
                return copy;
              });
            } else if (event.type === "sub_agent_tool") {
              setTurns((prev) => {
                const copy = [...prev];
                const turn = { ...copy[turnIndex] };
                const existing = turn.subAgents.find((s) => s.agent === event.agent);
                if (existing) {
                  turn.subAgents = turn.subAgents.map((s) =>
                    s.agent === event.agent ? { ...s, status: `using ${event.name}` } : s,
                  );
                } else {
                  turn.subAgents = [...turn.subAgents, { agent: event.agent, status: `using ${event.name}`, content: "" }];
                }
                copy[turnIndex] = turn;
                return copy;
              });
            } else if (event.type === "tool_call_result") {
              // If this is a sub-agent delegation result, mark that agent as done
              if (event.name.startsWith("call_")) {
                const agentName = event.name.replace("call_", "");
                setTurns((prev) => {
                  const copy = [...prev];
                  const turn = { ...copy[turnIndex] };
                  turn.subAgents = turn.subAgents.map((s) =>
                    s.agent === agentName ? { ...s, status: "done" } : s,
                  );
                  copy[turnIndex] = turn;
                  return copy;
                });
              }
            } else if (event.type === "done") {
              setTurns((prev) => {
                const copy = [...prev];
                copy[turnIndex] = {
                  ...copy[turnIndex],
                  assistantContent: event.response,
                  toolsCalled: event.tools_called,
                  turnsUsed: event.turns_used,
                  artifacts: (event.artifacts ?? []).filter((a: StreamArtifact) => a.code || a.output),
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
            {/* Sub-agent activity */}
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
