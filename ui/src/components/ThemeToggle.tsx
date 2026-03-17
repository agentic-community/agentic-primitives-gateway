import { useCallback, useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { type Theme, getStoredTheme, setStoredTheme } from "../lib/theme";

export default function ThemeToggle() {
  const { user } = useAuth();
  const userId = user?.profile?.sub;
  const [theme, setTheme] = useState<Theme>(() => getStoredTheme(userId));

  const toggle = useCallback(() => {
    const next = theme === "dark" ? "light" : "dark";
    setStoredTheme(next, userId);
    setTheme(next);
  }, [theme, userId]);

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
