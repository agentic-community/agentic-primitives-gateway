const STORAGE_KEY = "apg-theme";

export type Theme = "light" | "dark";

export function getStoredTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function setStoredTheme(theme: Theme) {
  localStorage.setItem(STORAGE_KEY, theme);
  applyTheme(theme);
}

export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

/** Shared syntax highlighter theme for Prism. */
export const CODE_THEME: Record<string, React.CSSProperties> = {
  'pre[class*="language-"]': {
    background: "transparent",
    margin: 0,
    padding: 0,
    fontSize: "0.8125rem",
    lineHeight: "1.5",
  },
  'code[class*="language-"]': {
    background: "transparent",
    fontSize: "0.8125rem",
  },
  comment: { color: "#6b7280" },
  string: { color: "#059669" },
  keyword: { color: "#7c3aed" },
  number: { color: "#d97706" },
  function: { color: "#2563eb" },
  operator: { color: "#6b7280" },
  punctuation: { color: "#6b7280" },
  "class-name": { color: "#0891b2" },
  builtin: { color: "#0891b2" },
};

/** Prose classes for compact markdown rendering. */
export const PROSE_CLASSES =
  "prose prose-sm dark:prose-invert max-w-none prose-p:my-1 prose-pre:my-2 prose-ul:my-1 prose-ol:my-1 prose-li:my-0 prose-headings:my-2";

/** Tighter prose for sub-agent and small blocks. */
export const PROSE_CLASSES_COMPACT =
  "prose prose-xs dark:prose-invert max-w-none prose-p:my-0.5 prose-pre:my-1 prose-ul:my-0.5 prose-ol:my-0.5 prose-li:my-0 prose-headings:my-1 prose-code:text-[11px]";
