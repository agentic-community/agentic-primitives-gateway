import { useState } from "react";
import { cn } from "../lib/cn";

export default function ToolCallBlock({ tools }: { tools: string[] }) {
  const [open, setOpen] = useState(false);

  if (tools.length === 0) return null;

  return (
    <div className="rounded border border-gray-200 dark:border-gray-800 text-xs">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-900"
      >
        <span
          className={cn(
            "transition-transform text-[10px]",
            open && "rotate-90",
          )}
        >
          &#9654;
        </span>
        <span className="font-medium">
          {tools.length} tool call{tools.length !== 1 && "s"}
        </span>
      </button>
      {open && (
        <div className="border-t border-gray-200 dark:border-gray-800 px-3 py-2 space-y-1">
          {tools.map((tool, i) => (
            <div key={i} className="font-mono text-gray-600 dark:text-gray-400">
              {tool}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
