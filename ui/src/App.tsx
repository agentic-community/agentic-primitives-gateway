import { BrowserRouter, Route, Routes } from "react-router-dom";
import AuthProvider, { useAuth } from "./auth/AuthProvider";
import Layout from "./components/Layout";
import AgentChat from "./pages/AgentChat";
import AgentList from "./pages/AgentList";
import Dashboard from "./pages/Dashboard";
import PolicyManager from "./pages/PolicyManager";
import PrimitiveExplorer from "./pages/PrimitiveExplorer";
import Settings from "./pages/Settings";
import TeamList from "./pages/TeamList";
import TeamRun from "./pages/TeamRun";

/** Blocks rendering until auth is resolved (token set or noop confirmed). */
function AuthGate({ children }: { children: React.ReactNode }) {
  const { loading } = useAuth();

  // Always wait for auth initialization to complete.
  // This prevents API calls firing before the token is available.
  // For noop/api_key, loading resolves almost instantly.
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
                <Route path="settings" element={<Settings />} />
              </Route>
              {/* Callback route is handled inside AuthProvider before rendering */}
              <Route path="callback" element={<CallbackPage />} />
            </Routes>
          </AuthGate>
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
