import { NavLink } from "react-router-dom";
import { cn } from "../lib/cn";
import ThemeToggle from "./ThemeToggle";

const links = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/agents", label: "Agents", end: false },
  { to: "/explorer", label: "Explorer", end: false },
  { to: "/policies", label: "Policies", end: false },
  { to: "/docs", label: "API Docs", external: true },
];

export default function Sidebar() {
  return (
    <aside className="flex w-56 flex-col border-r border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900">
      <div className="px-4 py-4 border-b border-gray-200 dark:border-gray-800">
        <h1 className="text-sm font-bold font-mono tracking-tight text-indigo-600 dark:text-indigo-400">
          APG
        </h1>
        <p className="text-[10px] text-gray-500 dark:text-gray-400 font-mono">
          Agentic Primitives Gateway
        </p>
      </div>

      <nav className="flex-1 px-2 py-3 space-y-0.5">
        {links.map((link) =>
          link.external ? (
            <a
              key={link.to}
              href={link.to}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 rounded px-3 py-1.5 text-sm text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-800"
            >
              {link.label}
              <span className="text-[10px]">{"\u2197"}</span>
            </a>
          ) : (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) =>
                cn(
                  "block rounded px-3 py-1.5 text-sm",
                  isActive
                    ? "bg-indigo-100 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300 font-medium"
                    : "text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-800",
                )
              }
            >
              {link.label}
            </NavLink>
          ),
        )}
      </nav>

      <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-800">
        <ThemeToggle />
      </div>
    </aside>
  );
}
