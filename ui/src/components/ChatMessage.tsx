import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { cn } from "../lib/cn";
import { CODE_THEME, PROSE_CLASSES } from "../lib/theme";

export default function ChatMessage({
  role,
  content,
}: {
  role: "user" | "assistant";
  content: string;
}) {
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
