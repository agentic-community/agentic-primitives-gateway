import { useEffect, useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import type { KnowledgeSearchStructured, RetrievedChunkView, StreamArtifact } from "../api/types";
import { CODE_THEME } from "../lib/theme";

interface CitationClickDetail {
  index: number;
}

export default function ArtifactBlock({ artifact }: { artifact: StreamArtifact }) {
  const label = artifact.tool_name.startsWith("call_")
    ? `${artifact.tool_name.replace("call_", "")} output`
    : artifact.tool_name;

  const knowledge = isKnowledgeSearch(artifact) ? (artifact.structured as unknown as KnowledgeSearchStructured) : null;
  // Default inline-citation artifacts to open so the ``#citation-N``
  // anchors from inline pills have something to scroll to.  Without
  // this, the ChunkCard carrying the target id isn't in the DOM and
  // the pill click appears to do nothing.
  const [open, setOpen] = useState(Boolean(knowledge?.inline));

  // If a user collapses the panel and then clicks a pill that targets
  // one of *our* chunks, pop back open so the scroll lands on a
  // rendered card.  Listening on the window keeps this decoupled from
  // the chat page — any CitationPill anywhere dispatches the same event.
  useEffect(() => {
    if (!knowledge || knowledge.chunks.length === 0) return;
    const ourIndices = new Set(
      knowledge.chunks.map((c) => c.citation_index).filter((i): i is number => typeof i === "number"),
    );
    if (ourIndices.size === 0) return;
    const listener = (e: Event) => {
      const detail = (e as CustomEvent<CitationClickDetail>).detail;
      if (detail && ourIndices.has(detail.index)) setOpen(true);
    };
    window.addEventListener("apg:citation-click", listener);
    return () => window.removeEventListener("apg:citation-click", listener);
  }, [knowledge]);

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
        {knowledge && (
          <span className="text-indigo-400 dark:text-indigo-500">
            {knowledge.chunks.length} source{knowledge.chunks.length === 1 ? "" : "s"}
          </span>
        )}
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
          {knowledge ? (
            <KnowledgeSourcesPanel structured={knowledge} />
          ) : artifact.output && (
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

function isKnowledgeSearch(artifact: StreamArtifact): boolean {
  if (!artifact.structured) return false;
  const kind = (artifact.structured as Record<string, unknown>).kind;
  return kind === "knowledge_search";
}

function KnowledgeSourcesPanel({ structured }: { structured: KnowledgeSearchStructured }) {
  if (structured.chunks.length === 0) {
    return (
      <div className="bg-gray-50 dark:bg-gray-900 px-3 py-2">
        <p className="text-[11px] text-gray-500 dark:text-gray-400">No sources returned for "{structured.query}".</p>
      </div>
    );
  }
  return (
    <div className="bg-gray-50 dark:bg-gray-900 px-3 py-2 space-y-2">
      <p className="text-[10px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500">
        Sources ({structured.chunks.length}) · <span className="font-normal normal-case">{structured.namespace}</span>
      </p>
      <div className="space-y-1.5">
        {structured.chunks.map((chunk, i) => (
          <ChunkCard key={chunk.chunk_id || i} chunk={chunk} />
        ))}
      </div>
    </div>
  );
}

function ChunkCard({ chunk }: { chunk: RetrievedChunkView }) {
  const citation = chunk.citations?.[0];
  const headerSource = citation?.source || (chunk.metadata?.source as string | undefined) || chunk.document_id || "(unnamed)";
  const page = citation?.page;
  const uri = citation?.uri;
  const citationIndex = chunk.citation_index;
  const anchorId = citationIndex !== undefined ? `citation-${citationIndex}` : undefined;
  // Inline-citation chunks default to expanded so jumping from a pill
  // lands on a readable card; non-inline cards stay collapsed to keep
  // the panel compact.
  const [expanded, setExpanded] = useState(citationIndex !== undefined);

  // Also pop open if the user clicks a pill for *this specific chunk*
  // after having manually collapsed it — we want consistent behavior
  // regardless of prior UI state.  A short flash confirms the landing.
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (citationIndex === undefined) return;
    const listener = (e: Event) => {
      const detail = (e as CustomEvent<CitationClickDetail>).detail;
      if (detail?.index === citationIndex) {
        setExpanded(true);
        setFlash(true);
        window.setTimeout(() => setFlash(false), 1200);
      }
    };
    window.addEventListener("apg:citation-click", listener);
    return () => window.removeEventListener("apg:citation-click", listener);
  }, [citationIndex]);

  return (
    <div
      id={anchorId}
      className={`rounded border bg-white dark:bg-gray-950 scroll-mt-16 transition-colors duration-300 ${
        flash
          ? "border-indigo-500 ring-2 ring-indigo-400/50"
          : "border-indigo-100 dark:border-indigo-900/40"
      }`}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left"
        aria-expanded={expanded}
      >
        <span className={`transition-transform text-[9px] ${expanded ? "rotate-90" : ""}`} aria-hidden="true">&#9654;</span>
        {citationIndex !== undefined && (
          <span className="inline-flex items-center justify-center h-4 min-w-[1rem] rounded-full bg-indigo-100 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 text-[9px] font-semibold px-1 tabular-nums">
            {citationIndex}
          </span>
        )}
        <span className="text-[10px] font-mono text-indigo-600 dark:text-indigo-400 tabular-nums">
          {chunk.score.toFixed(2)}
        </span>
        <span className="flex-1 truncate text-[11px] text-gray-700 dark:text-gray-300" title={headerSource}>
          {headerSource}
        </span>
        {page && (
          <span className="text-[10px] text-gray-500 dark:text-gray-400 font-mono">p.{page}</span>
        )}
      </button>
      {expanded && (
        <div className="border-t border-indigo-100 dark:border-indigo-900/40 px-2.5 py-2 space-y-2">
          <pre className="text-[11px] text-gray-600 dark:text-gray-400 whitespace-pre-wrap max-h-48 overflow-auto">{chunk.text}</pre>
          {uri && (
            <div className="text-[10px] text-gray-500 dark:text-gray-400 break-all">
              <span className="font-medium">URI: </span>
              <span className="font-mono">{uri}</span>
            </div>
          )}
          {chunk.metadata && Object.keys(chunk.metadata).length > 0 && (
            <details className="text-[10px] text-gray-500 dark:text-gray-400">
              <summary className="cursor-pointer select-none">Metadata</summary>
              <pre className="mt-1 font-mono whitespace-pre-wrap break-all">
                {JSON.stringify(chunk.metadata, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
