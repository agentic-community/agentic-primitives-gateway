import { NavLink, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { useConnectionStatus } from "../hooks/useConnectionStatus";
import { cn } from "../lib/cn";
import ThemeToggle from "./ThemeToggle";

const links = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/agents", label: "Agents", end: false },
  { to: "/teams", label: "Teams", end: false },
  { to: "/explorer", label: "Explorer", end: false },
  { to: "/policies", label: "Policies", end: false },
  { to: "/settings", label: "Settings", end: false },
  { to: "/docs", label: "API Docs", external: true },
];

// Links rendered only for principals with the admin scope (via /whoami).
const adminLinks = [{ to: "/audit", label: "Audit", end: false }];

export default function Sidebar() {
  const { user, logout, backend, isAdmin } = useAuth();
  const username = user?.profile?.preferred_username || user?.profile?.name || user?.profile?.sub || "";
  const location = useLocation();
  const activeAgent = decodeURIComponent(location.pathname.match(/\/agents\/([^/]+)\/chat/)?.[1] ?? "");
  const activeTeam = decodeURIComponent(location.pathname.match(/\/teams\/([^/]+)\/run/)?.[1] ?? "");
  const connectionStatus = useConnectionStatus();

  return (
    <aside className="flex w-56 flex-col border-r border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900">
      <div className="px-4 py-4 border-b border-gray-200 dark:border-gray-800">
        <h1 className="text-sm font-bold font-mono tracking-tight text-indigo-600 dark:text-indigo-400">
          APG
        </h1>
        <p className="text-[10px] text-gray-500 dark:text-gray-400 font-mono">
          Agentic Primitives Gateway
        </p>
        <div className="flex items-center gap-1.5 mt-1.5">
          <span
            className={cn(
              "inline-block w-1.5 h-1.5 rounded-full",
              connectionStatus === "connected" && "bg-green-500",
              connectionStatus === "disconnected" && "bg-red-500 animate-pulse",
              connectionStatus === "connecting" && "bg-yellow-500 animate-pulse",
            )}
          />
          <span
            className={cn(
              "text-[10px] font-mono",
              connectionStatus === "connected" && "text-green-600 dark:text-green-400",
              connectionStatus === "disconnected" && "text-red-500 dark:text-red-400",
              connectionStatus === "connecting" && "text-yellow-600 dark:text-yellow-400",
            )}
          >
            {connectionStatus === "connected" && "Connected"}
            {connectionStatus === "disconnected" && "Server offline"}
            {connectionStatus === "connecting" && "Connecting..."}
          </span>
        </div>
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
              <span className="flex items-center justify-between w-full">
                <span>{link.label}</span>
                {link.label === "Agents" && activeAgent && (
                  <span className="flex items-center gap-1 text-[10px] font-mono text-indigo-500 dark:text-indigo-400 truncate max-w-[5rem]">
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse shrink-0" />
                    {activeAgent}
                  </span>
                )}
                {link.label === "Teams" && activeTeam && (
                  <span className="flex items-center gap-1 text-[10px] font-mono text-indigo-500 dark:text-indigo-400 truncate max-w-[5rem]">
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse shrink-0" />
                    {activeTeam}
                  </span>
                )}
              </span>
            </NavLink>
          ),
        )}
        {isAdmin && (
          <>
            <div className="mt-3 px-3 pb-1 text-[10px] font-mono uppercase tracking-wider text-gray-400 dark:text-gray-500">
              Admin
            </div>
            {adminLinks.map((link) => (
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
            ))}
          </>
        )}
      </nav>

      <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-800 space-y-2">
        {backend === "jwt" && username && (
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-500 dark:text-gray-400 truncate" title={username}>
              {username}
            </span>
            <button
              onClick={logout}
              className="text-[10px] text-gray-400 hover:text-red-500 dark:hover:text-red-400"
            >
              Logout
            </button>
          </div>
        )}
        <ThemeToggle />
      </div>
    </aside>
  );
}
