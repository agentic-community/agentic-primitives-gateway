import { Children, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import type { RetrievedChunkView } from "../api/types";
import { cn } from "../lib/cn";
import { CODE_THEME, PROSE_CLASSES } from "../lib/theme";

const CITATION_MARKER_PATTERN = /\[(\d+)\]/g;

export default function ChatMessage({
  role,
  content,
  citationsByIndex,
}: {
  role: "user" | "assistant";
  content: string;
  // When the agent runs with inline_citations enabled, the parent
  // page collects RetrievedChunkView entries keyed by their global
  // citation_index and passes them here so the renderer can turn
  // ``[N]`` tokens into pills linked to the source panel.
  citationsByIndex?: Map<number, RetrievedChunkView>;
}) {
  const renderChildren = (children: ReactNode): ReactNode =>
    Children.map(children, (child) => {
      if (typeof child === "string") return decorateCitations(child, citationsByIndex);
      return child;
    });

  return (
    <div
      className={cn(
        "rounded-lg px-4 py-3 text-sm",
        role === "user"
          ? "bg-indigo-50 dark:bg-indigo-950/40 text-gray-900 dark:text-gray-100 ml-12"
          : "bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-gray-100 mr-12",
      )}
    >
      <span className="text-[10px] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500 block mb-1">
        {role}
      </span>
      {role === "user" ? (
        <span className="whitespace-pre-wrap">{content}</span>
      ) : (
        <div className={PROSE_CLASSES}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              p({ children }) {
                return <p>{renderChildren(children)}</p>;
              },
              li({ children }) {
                return <li>{renderChildren(children)}</li>;
              },
              code({ className, children, ...props }) {
                const match = /language-(\w+)/.exec(className || "");
                const code = String(children).replace(/\n$/, "");
                if (match) {
                  return (
                    <SyntaxHighlighter
                      style={CODE_THEME}
                      language={match[1]}
                      PreTag="div"
                      className="rounded border border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-800 p-3 overflow-x-auto"
                    >
                      {code}
                    </SyntaxHighlighter>
                  );
                }
                return (
                  <code
                    className="rounded bg-gray-200 dark:bg-gray-700 px-1 py-0.5 text-[0.8125rem] font-mono"
                    {...props}
                  >
                    {children}
                  </code>
                );
              },
              pre({ children }) {
                return <>{children}</>;
              },
            }}
          >
            {content}
          </ReactMarkdown>
        </div>
      )}
    </div>
  );
}

function decorateCitations(text: string, map?: Map<number, RetrievedChunkView>): ReactNode {
  if (!map || map.size === 0) return text;
  const parts: ReactNode[] = [];
  let lastEnd = 0;
  for (const match of text.matchAll(CITATION_MARKER_PATTERN)) {
    const idx = Number(match[1]);
    const chunk = map.get(idx);
    if (chunk === undefined) continue; // not a citation we know about — leave as text
    const start = match.index ?? 0;
    if (start > lastEnd) parts.push(text.slice(lastEnd, start));
    parts.push(<CitationPill key={`c-${start}`} index={idx} chunk={chunk} />);
    lastEnd = start + match[0].length;
  }
  if (parts.length === 0) return text;
  if (lastEnd < text.length) parts.push(text.slice(lastEnd));
  return parts;
}

function CitationPill({ index, chunk }: { index: number; chunk: RetrievedChunkView }) {
  const citation = chunk.citations?.[0];
  const source = citation?.source || (chunk.metadata?.source as string | undefined) || chunk.document_id;
  const page = citation?.page;
  const label = page ? `${source} · p.${page}` : source || `source ${index}`;

  // Custom click: dispatch a window event the citation card listens for,
  // then scroll once the card has rendered.  Going through an event
  // (rather than just the ``href`` fragment) lets the target card pop
  // itself open even if the user had manually collapsed the panel —
  // otherwise the browser scrolls to an element that isn't in layout
  // and nothing visible happens.
  const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    window.dispatchEvent(new CustomEvent("apg:citation-click", { detail: { index } }));
    // Defer the scroll so the listener has a tick to expand the card
    // (state update + re-render) before the browser measures layout.
    window.setTimeout(() => {
      const el = document.getElementById(`citation-${index}`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 30);
  };

  return (
    <a
      href={`#citation-${index}`}
      onClick={handleClick}
      title={label}
      className="inline-flex items-center justify-center align-baseline mx-0.5 h-[1.4em] min-w-[1.4em] rounded-full bg-indigo-100 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 text-[0.7em] font-semibold no-underline hover:bg-indigo-200 dark:hover:bg-indigo-900 transition-colors px-1 cursor-pointer"
    >
      {index}
    </a>
  );
}
