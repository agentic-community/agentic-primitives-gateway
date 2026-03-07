import { useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import type { StreamArtifact } from "../api/types";
import { CODE_THEME } from "../lib/theme";

export default function ArtifactBlock({ artifact }: { artifact: StreamArtifact }) {
  const [open, setOpen] = useState(false);
  const label = artifact.tool_name.startsWith("call_")
    ? `${artifact.tool_name.replace("call_", "")} output`
    : artifact.tool_name;

  return (
    <div className="rounded-lg border border-indigo-200 dark:border-indigo-900/50 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-label={`${open ? "Collapse" : "Expand"} ${label}`}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-[11px] text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50/50 dark:hover:bg-indigo-950/30 transition-colors"
      >
        <span className={`transition-transform text-[10px] ${open ? "rotate-90" : ""}`} aria-hidden="true">&#9654;</span>
        <span className="font-medium">{label}</span>
        {artifact.code && <span className="text-indigo-400 dark:text-indigo-500 font-mono">{artifact.language}</span>}
      </button>
      {open && (
        <div className="border-t border-indigo-200 dark:border-indigo-900/50">
          {artifact.code && (
            <div className="max-h-96 overflow-auto">
              <SyntaxHighlighter
                style={CODE_THEME}
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
