import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import {
  AUDIT_OUTCOMES,
  AUDIT_RESOURCE_TYPES,
  type AuditFilters,
  type AuditStatus,
} from "../api/types";
import AuditEventRow from "../components/AuditEventRow";
import { useAuditHistory } from "../hooks/useAuditHistory";
import { useAuditStream } from "../hooks/useAuditStream";
import { cn } from "../lib/cn";

type Mode = "historical" | "live";

export default function Audit() {
  const [mode, setMode] = useState<Mode>("live");
  const [paused, setPaused] = useState(false);
  const [filters, setFilters] = useState<AuditFilters>({});
  const [status, setStatus] = useState<AuditStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  // Poll /audit/status once on mount + when mode flips so the UI learns
  // if the sink was just configured (or unconfigured).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await api.auditStatus();
        if (!cancelled) setStatus(s);
      } catch (e) {
        if (!cancelled)
          setStatusError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [mode]);

  const sinkConfigured = status?.stream_sink_configured ?? true;

  return (
    <div className="flex flex-col h-full">
      <header className="border-b border-gray-200 dark:border-gray-800 px-6 py-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              Audit
            </h1>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              {status?.stream_sink_configured
                ? `Stream ${status.stream_name} — ${status.length ?? "?"} event${
                    status.length === 1 ? "" : "s"
                  } (MAXLEN ${status.maxlen?.toLocaleString() ?? "?"})`
                : "Audit stream sink not configured — see setup guide below"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <ModeToggle mode={mode} onChange={setMode} />
          </div>
        </div>
        <FilterBar filters={filters} onChange={setFilters} />
      </header>

      {!sinkConfigured ? (
        <EmptyState error={statusError} />
      ) : mode === "live" ? (
        <LiveTail filters={filters} paused={paused} onPauseToggle={() => setPaused((p) => !p)} />
      ) : (
        <HistoricalBrowse filters={filters} />
      )}
    </div>
  );
}

function ModeToggle({
  mode,
  onChange,
}: {
  mode: Mode;
  onChange: (mode: Mode) => void;
}) {
  return (
    <div className="inline-flex rounded border border-gray-200 dark:border-gray-800 overflow-hidden text-xs font-mono">
      {(["live", "historical"] as Mode[]).map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => onChange(m)}
          className={cn(
            "px-3 py-1.5",
            mode === m
              ? "bg-indigo-600 text-white"
              : "bg-white dark:bg-gray-900 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800",
          )}
        >
          {m === "live" ? "Live tail" : "Historical"}
        </button>
      ))}
    </div>
  );
}

function FilterBar({
  filters,
  onChange,
}: {
  filters: AuditFilters;
  onChange: (next: AuditFilters) => void;
}) {
  const patch = (k: keyof AuditFilters, v: string) => {
    const next = { ...filters };
    if (!v) {
      delete next[k];
    } else {
      (next as Record<string, string>)[k] = v;
    }
    onChange(next);
  };

  const hasFilters = Object.values(filters).some((v) => v !== undefined && v !== "");

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <input
        type="text"
        placeholder="action (e.g. policy.deny)"
        value={filters.action ?? ""}
        onChange={(e) => patch("action", e.target.value)}
        className={filterInputClass}
      />
      <input
        type="text"
        placeholder="category (e.g. policy)"
        value={filters.action_category ?? ""}
        onChange={(e) => patch("action_category", e.target.value)}
        className={filterInputClass}
      />
      <select
        value={filters.outcome ?? ""}
        onChange={(e) => patch("outcome", e.target.value)}
        className={filterInputClass}
      >
        <option value="">any outcome</option>
        {AUDIT_OUTCOMES.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
      <select
        value={filters.resource_type ?? ""}
        onChange={(e) => patch("resource_type", e.target.value)}
        className={filterInputClass}
      >
        <option value="">any resource</option>
        {AUDIT_RESOURCE_TYPES.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>
      <input
        type="text"
        placeholder="actor_id"
        value={filters.actor_id ?? ""}
        onChange={(e) => patch("actor_id", e.target.value)}
        className={filterInputClass}
      />
      <input
        type="text"
        placeholder="correlation_id"
        value={filters.correlation_id ?? ""}
        onChange={(e) => patch("correlation_id", e.target.value)}
        className={filterInputClass}
      />
      {hasFilters && (
        <button
          type="button"
          onClick={() => onChange({})}
          className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
        >
          Clear
        </button>
      )}
    </div>
  );
}

const filterInputClass =
  "rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-indigo-500";

function LiveTail({
  filters,
  paused,
  onPauseToggle,
}: {
  filters: AuditFilters;
  paused: boolean;
  onPauseToggle: () => void;
}) {
  const { events, status, dropped, clear, error } = useAuditStream(filters, {
    paused,
  });

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <div className="flex items-center justify-between px-6 py-2 border-b border-gray-200 dark:border-gray-800 text-xs text-gray-500 dark:text-gray-400">
        <div className="flex items-center gap-3">
          <StreamStatusDot status={status} />
          <span className="font-mono">
            {events.length} event{events.length === 1 ? "" : "s"}
            {dropped > 0 && ` • ${dropped} dropped`}
          </span>
          {error && (
            <span className="text-red-600 dark:text-red-400">{error}</span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onPauseToggle}
            className="px-2 py-1 rounded border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800 font-mono"
          >
            {paused ? "▶ Resume" : "❚❚ Pause"}
          </button>
          <button
            type="button"
            onClick={clear}
            className="px-2 py-1 rounded border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800 font-mono"
          >
            Clear
          </button>
        </div>
      </div>
      <EventList events={events} empty="Waiting for events…" />
    </div>
  );
}

function HistoricalBrowse({ filters }: { filters: AuditFilters }) {
  const { events, loading, loadingMore, exhausted, loadOlder, refresh, error } =
    useAuditHistory(filters);

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <div className="flex items-center justify-between px-6 py-2 border-b border-gray-200 dark:border-gray-800 text-xs text-gray-500 dark:text-gray-400">
        <span className="font-mono">
          {events.length} event{events.length === 1 ? "" : "s"}
          {exhausted && events.length > 0 && " (end of stream)"}
        </span>
        {error && (
          <span className="text-red-600 dark:text-red-400">{error}</span>
        )}
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="px-2 py-1 rounded border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800 font-mono disabled:opacity-50"
        >
          Refresh
        </button>
      </div>
      <EventList events={events} empty={loading ? "Loading…" : "No events"} />
      {!exhausted && (
        <div className="border-t border-gray-200 dark:border-gray-800 px-6 py-3 text-center">
          <button
            type="button"
            onClick={loadOlder}
            disabled={loadingMore}
            className="px-3 py-1.5 text-xs rounded border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800 font-mono disabled:opacity-50"
          >
            {loadingMore ? "Loading…" : "Load older"}
          </button>
        </div>
      )}
    </div>
  );
}

function EventList({
  events,
  empty,
}: {
  events: ReturnType<typeof useAuditHistory>["events"];
  empty: string;
}) {
  if (events.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-gray-500 dark:text-gray-400">
        {empty}
      </div>
    );
  }
  return (
    <div className="flex-1 overflow-y-auto px-6 py-3 space-y-1.5">
      {events.map((evt) => (
        <AuditEventRow key={evt.event_id} event={evt} />
      ))}
    </div>
  );
}

function StreamStatusDot({
  status,
}: {
  status: ReturnType<typeof useAuditStream>["status"];
}) {
  const { label, color } = useMemo(() => {
    switch (status) {
      case "open":
        return { label: "Live", color: "bg-green-500" };
      case "connecting":
        return { label: "Connecting…", color: "bg-yellow-500 animate-pulse" };
      case "paused":
        return { label: "Paused", color: "bg-gray-400" };
      case "error":
        return { label: "Error", color: "bg-red-500" };
      default:
        return { label: "Idle", color: "bg-gray-300" };
    }
  }, [status]);
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn("inline-block h-2 w-2 rounded-full", color)} />
      <span className="font-mono">{label}</span>
    </span>
  );
}

function EmptyState({ error }: { error: string | null }) {
  return (
    <div className="flex-1 overflow-y-auto px-6 py-8">
      <div className="max-w-2xl mx-auto rounded-lg border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/20 p-6">
        <h2 className="text-base font-semibold text-amber-800 dark:text-amber-200">
          Audit stream sink not configured
        </h2>
        <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">
          The audit viewer reads from the <code>redis_stream</code> sink.
          Enable it in your server config to populate this page.
        </p>
        {error && (
          <p className="mt-2 text-xs text-red-600 dark:text-red-400 font-mono">
            {error}
          </p>
        )}
        <pre className="mt-4 rounded bg-gray-900 text-gray-100 p-3 text-xs overflow-x-auto">
{`audit:
  enabled: true
  stdout_json: true
  sinks:
    - name: durable
      backend: redis_stream
      config:
        redis_url: "redis://localhost:6379/0"
        stream: "gateway:audit"
        maxlen: 100000`}
        </pre>
        <p className="mt-4 text-xs text-amber-700 dark:text-amber-400">
          Restart the server after updating the config. See the{" "}
          <a
            href="https://agentic-community.github.io/agentic-primitives-gateway/guides/observability/"
            target="_blank"
            rel="noopener noreferrer"
            className="underline"
          >
            observability guide
          </a>{" "}
          for log-shipping and SIEM integration patterns.
        </p>
      </div>
    </div>
  );
}
