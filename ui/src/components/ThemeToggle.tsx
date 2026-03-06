import { useCallback, useState } from "react";
import { type Theme, getStoredTheme, setStoredTheme } from "../lib/theme";

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(getStoredTheme);

  const toggle = useCallback(() => {
    const next = theme === "dark" ? "light" : "dark";
    setStoredTheme(next);
    setTheme(next);
  }, [theme]);

  return (
    <button
      onClick={toggle}
      className="text-xs text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
      title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      {theme === "dark" ? "Light mode" : "Dark mode"}
    </button>
  );
}
