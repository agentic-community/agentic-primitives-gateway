import { useEffect } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { setApiAuthToken } from "./api/client";
import AuthProvider, { useAuth } from "./auth/AuthProvider";
import Layout from "./components/Layout";
import AgentChat from "./pages/AgentChat";
import AgentList from "./pages/AgentList";
import Dashboard from "./pages/Dashboard";
import PolicyManager from "./pages/PolicyManager";
import PrimitiveExplorer from "./pages/PrimitiveExplorer";
import TeamList from "./pages/TeamList";
import TeamRun from "./pages/TeamRun";

/** Syncs the auth token into the API client whenever it changes. */
function TokenSync({ children }: { children: React.ReactNode }) {
  const { token } = useAuth();
  useEffect(() => {
    setApiAuthToken(token);
  }, [token]);
  return <>{children}</>;
}

/** Shows a loading screen while auth is initializing. */
function AuthGate({ children }: { children: React.ReactNode }) {
  const { loading, backend } = useAuth();

  // Noop auth never shows a loading screen
  if (backend === "noop" || backend === "api_key") {
    return <>{children}</>;
  }

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-zinc-950 text-zinc-400">
        <div className="text-center">
          <div className="mb-4 h-8 w-8 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-300 mx-auto" />
          <p>Authenticating...</p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}

export default function App() {
  return (
    <BrowserRouter basename="/ui">
      <AuthProvider>
        <TokenSync>
          <AuthGate>
            <Routes>
              <Route element={<Layout />}>
                <Route index element={<Dashboard />} />
                <Route path="agents" element={<AgentList />} />
                <Route path="agents/:name/chat" element={<AgentChat />} />
                <Route path="teams" element={<TeamList />} />
                <Route path="teams/:name/run" element={<TeamRun />} />
                <Route path="policies" element={<PolicyManager />} />
                <Route path="explorer" element={<PrimitiveExplorer />} />
              </Route>
              {/* Callback route is handled inside AuthProvider before rendering */}
              <Route path="callback" element={<CallbackPage />} />
            </Routes>
          </AuthGate>
        </TokenSync>
      </AuthProvider>
    </BrowserRouter>
  );
}

/** Minimal callback page — AuthProvider handles the actual token exchange. */
function CallbackPage() {
  return (
    <div className="flex h-screen items-center justify-center bg-zinc-950 text-zinc-400">
      <div className="text-center">
        <div className="mb-4 h-8 w-8 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-300 mx-auto" />
        <p>Completing login...</p>
      </div>
    </div>
  );
}
