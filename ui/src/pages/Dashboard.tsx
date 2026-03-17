import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AgentSpec, CredentialStatusResponse } from "../api/types";
import AgentCard from "../components/AgentCard";
import HealthBadge from "../components/HealthBadge";
import LoadingSpinner from "../components/LoadingSpinner";
import ProviderCard from "../components/ProviderCard";
import { useHealth } from "../hooks/useHealth";
import { useProviders } from "../hooks/useProviders";

export default function Dashboard() {
  const { health, readiness, loading: healthLoading } = useHealth();
  const { providers, loading: providersLoading } = useProviders();
  const [agents, setAgents] = useState<AgentSpec[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [credStatus, setCredStatus] = useState<CredentialStatusResponse | null>(null);

  useEffect(() => {
    api
      .listAgents()
      .then(setAgents)
      .catch(() => {})
      .finally(() => setAgentsLoading(false));
    api.credentialStatus().then(setCredStatus).catch(() => {});
  }, []);

  const loading = healthLoading || providersLoading || agentsLoading;
  if (loading) return <LoadingSpinner className="mt-32" />;

  return (
    <div className="max-w-5xl space-y-8">
      {/* Health */}
      <section>
        <h2 className="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">
          Health
        </h2>
        <div className="flex flex-wrap gap-2">
          {health && <HealthBadge status={health.status} label="Liveness" />}
          {readiness && (
            <HealthBadge status={readiness.status} label="Readiness" />
          )}
          {readiness?.config_reload_error && (
            <span className="text-xs text-yellow-600 dark:text-yellow-400">
              Config reload error: {readiness.config_reload_error}
            </span>
          )}
        </div>
      </section>

      {/* Credential setup banner */}
      {credStatus && credStatus.server_credentials === "never" && credStatus.required_credentials.length > 0 && (
        <section>
          <div className="rounded-lg border border-yellow-300 dark:border-yellow-700 bg-yellow-50 dark:bg-yellow-950/30 px-4 py-3">
            <p className="text-sm font-medium text-yellow-800 dark:text-yellow-300">
              Credentials required
            </p>
            <p className="mt-1 text-xs text-yellow-700 dark:text-yellow-400">
              Server credentials are disabled. The active providers require:{" "}
              <span className="font-mono font-medium">
                {credStatus.required_credentials.join(", ")}
              </span>.{" "}
              <Link to="/settings" className="underline font-medium hover:text-yellow-900 dark:hover:text-yellow-200">
                Configure your credentials in Settings
              </Link>
            </p>
          </div>
        </section>
      )}

      {/* Providers */}
      {providers && (
        <section>
          <h2 className="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">
            Providers
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {Object.entries(providers).map(([primitive, info]) => (
              <ProviderCard
                key={primitive}
                primitive={primitive}
                info={info}
                checks={readiness?.checks}
              />
            ))}
          </div>
        </section>
      )}

      {/* Agents */}
      <section>
        <h2 className="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">
          Agents
        </h2>
        {agents.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            No agents configured.
          </p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {agents.map((agent) => (
              <AgentCard key={agent.name} agent={agent} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
