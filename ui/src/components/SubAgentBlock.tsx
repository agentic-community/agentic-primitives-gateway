import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PROSE_CLASSES_COMPACT } from "../lib/theme";

export interface SubAgentActivity {
  agent: string;
  status: string;
  content: string;
}

export default function SubAgentBlock({ activity }: { activity: SubAgentActivity }) {
  const isDone = activity.status === "done";
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
        aria-expanded={open}
        aria-label={`${open ? "Collapse" : "Expand"} ${activity.agent} agent`}
        className="flex w-full items-center gap-2 px-3 py-1.5 hover:bg-black/5 dark:hover:bg-white/5 transition-colors"
      >
        <span
          className={`transition-transform text-[10px] ${isDone ? "text-green-500" : "text-rose-500"} ${open ? "rotate-90" : ""}`}
          aria-hidden="true"
        >
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
          <div className={`text-[11px] text-gray-600 dark:text-gray-400 max-h-64 overflow-y-auto overflow-x-hidden break-words ${PROSE_CLASSES_COMPACT}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {activity.content}
            </ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}
