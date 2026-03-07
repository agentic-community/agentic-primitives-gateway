import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import AgentChat from "./pages/AgentChat";
import AgentList from "./pages/AgentList";
import Dashboard from "./pages/Dashboard";
import PolicyManager from "./pages/PolicyManager";
import PrimitiveExplorer from "./pages/PrimitiveExplorer";
import TeamList from "./pages/TeamList";
import TeamRun from "./pages/TeamRun";

export default function App() {
  return (
    <BrowserRouter basename="/ui">
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
      </Routes>
    </BrowserRouter>
  );
}
