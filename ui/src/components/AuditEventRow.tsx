import { useState } from "react";
import type { AuditEvent, AuditOutcome } from "../api/types";
import { cn } from "../lib/cn";

const OUTCOME_COLORS: Record<AuditOutcome, string> = {
  allow: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  success: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  deny: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  failure: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  error: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
  // New enum value from the server: providers explicitly declining an
  // optional operation.  Neutral color — it's not a failure.
  not_implemented: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
};

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts);
    return (
      d.toLocaleTimeString(undefined, { hour12: false }) +
      "." +
      String(d.getMilliseconds()).padStart(3, "0")
    );
  } catch {
    return ts;
  }
}

function formatDate(ts: string): string {
  try {
    return new Date(ts).toLocaleDateString();
  } catch {
    return "";
  }
}

export default function AuditEventRow({ event }: { event: AuditEvent }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const outcomeClass =
    OUTCOME_COLORS[event.outcome] ??
    "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300";

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(event, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore clipboard permission errors — non-critical
    }
  };

  return (
    <div className="border border-gray-200 dark:border-gray-800 rounded">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-3 py-1.5 text-left text-xs font-mono hover:bg-gray-50 dark:hover:bg-gray-900/40"
      >
        <span
          className={cn(
            "inline-block text-[10px]",
            open ? "rotate-90" : "",
            "transition-transform",
          )}
        >
          ▶
        </span>
        <span className="tabular-nums text-gray-500 dark:text-gray-400 shrink-0 w-20">
          {formatTimestamp(event.timestamp)}
        </span>
        <span
          className={cn(
            "inline-block shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide",
            outcomeClass,
          )}
        >
          {event.outcome}
        </span>
        <span className="font-medium text-gray-900 dark:text-gray-100 shrink-0">
          {event.action}
        </span>
        {event.actor_id && (
          <span
            className="text-gray-600 dark:text-gray-400 truncate"
            title={`${event.actor_type ?? "?"}:${event.actor_id}`}
          >
            {event.actor_id}
          </span>
        )}
        {event.resource_id && (
          <span className="text-gray-500 dark:text-gray-500 truncate">
            {event.resource_type ? `${event.resource_type}:` : ""}
            {event.resource_id}
          </span>
        )}
        {event.duration_ms !== null && event.duration_ms !== undefined && (
          <span className="ml-auto text-gray-400 dark:text-gray-500 tabular-nums shrink-0">
            {event.duration_ms.toFixed(1)}ms
          </span>
        )}
      </button>

      {open && (
        <div className="border-t border-gray-200 dark:border-gray-800 px-3 py-2 text-xs">
          <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono mb-2">
            <Cell label="Date" value={formatDate(event.timestamp)} />
            <Cell label="Event ID" value={event.event_id} />
            {event.request_id && (
              <Cell label="request_id" value={event.request_id} />
            )}
            {event.correlation_id && (
              <Cell label="correlation_id" value={event.correlation_id} />
            )}
            {event.actor_groups.length > 0 && (
              <Cell label="groups" value={event.actor_groups.join(", ")} />
            )}
            {event.source_ip && (
              <Cell label="source_ip" value={event.source_ip} />
            )}
            {event.user_agent && (
              <Cell label="user_agent" value={event.user_agent} />
            )}
            {event.http_method && (
              <Cell
                label="http"
                value={`${event.http_method} ${event.http_path ?? ""} → ${
                  event.http_status ?? "—"
                }`}
              />
            )}
            {event.reason && <Cell label="reason" value={event.reason} />}
          </div>
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
              Full event
            </span>
            <button
              type="button"
              onClick={handleCopy}
              className="text-[10px] text-indigo-600 dark:text-indigo-400 hover:underline"
            >
              {copied ? "Copied" : "Copy JSON"}
            </button>
          </div>
          <pre className="bg-gray-50 dark:bg-gray-900 rounded p-2 text-[11px] overflow-x-auto">
            {JSON.stringify(event, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <>
      <span className="text-gray-500 dark:text-gray-400 whitespace-nowrap">
        {label}
      </span>
      <span className="text-gray-800 dark:text-gray-200 break-all">{value}</span>
    </>
  );
}
