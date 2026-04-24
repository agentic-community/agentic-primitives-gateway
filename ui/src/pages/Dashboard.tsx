import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AgentSpec, CredentialStatusResponse } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import AgentCard from "../components/AgentCard";
import HealthBadge from "../components/HealthBadge";
import LoadingSpinner from "../components/LoadingSpinner";
import ProviderCard from "../components/ProviderCard";
import { useHealth } from "../hooks/useHealth";
import { useProviders } from "../hooks/useProviders";

/** Derive a single Readiness status from a provider checks dict. Any
 *  provider reporting "down" degrades readiness; anything else (ok,
 *  reachable, timeout) is treated as non-blocking for the top-level
 *  badge. Per-provider nuance is surfaced on the ProviderCard chips.
 *  Exported so it can be unit-tested without mounting the Dashboard.
 */
export function deriveReadiness(
  checks: Record<string, string> | null,
): "ok" | "degraded" | null {
  if (!checks) return null;
  return Object.values(checks).some((s) => s === "down") ? "degraded" : "ok";
}

export default function Dashboard() {
  const { health, loading: healthLoading } = useHealth();
  const { providers, loading: providersLoading } = useProviders();
  const { principalLoaded, principalId } = useAuth();
  const [agents, setAgents] = useState<AgentSpec[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [credStatus, setCredStatus] = useState<CredentialStatusResponse | null>(null);
  const [checks, setChecks] = useState<Record<string, string> | null>(null);
  const [checksLoading, setChecksLoading] = useState(true);

  // All authenticated API calls are gated on principalLoaded rather than
  // the OIDC `user` object. `user` is null in noop + api_key deployments
  // even when the caller is fully identified server-side; principalLoaded
  // flips true after /whoami resolves, so it's the one signal that works
  // across all three auth backends. The server-side effect is that every
  // `provider.healthcheck` audit event is attributed to principalId (e.g.
  // "noop", an api_key principal, or an OIDC subject) rather than
  // "anonymous", which was the bug this refactor fixes.
  useEffect(() => {
    if (!principalLoaded) return;
    api
      .listAgents()
      .then(setAgents)
      .catch(() => {})
      .finally(() => setAgentsLoading(false));
    api.credentialStatus().then(setCredStatus).catch(() => {});
    api
      .providerStatus()
      .then((r) => setChecks(r.checks))
      .catch(() => setChecks({}))
      .finally(() => setChecksLoading(false));
  }, [principalLoaded, principalId]);

  const readinessStatus = useMemo(() => deriveReadiness(checks), [checks]);

  const stillNeedsCreds = checks
    ? Object.values(checks).some((s) => s === "reachable")
    : false;

  const loading =
    !principalLoaded ||
    healthLoading ||
    providersLoading ||
    agentsLoading ||
    checksLoading;
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
          {readinessStatus && (
            <HealthBadge status={readinessStatus} label="Readiness" />
          )}
        </div>
      </section>

      {/* Credential setup banner */}
      {credStatus && credStatus.server_credentials === "never" && credStatus.required_credentials.length > 0 && stillNeedsCreds && (
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
                checks={checks ?? undefined}
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
