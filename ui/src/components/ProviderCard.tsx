import type { ProviderInfo } from "../api/types";

export default function ProviderCard({
  primitive,
  info,
  checks,
}: {
  primitive: string;
  info: ProviderInfo;
  checks?: Record<string, string>;
}) {
  // Determine overall health for this primitive's providers
  const statuses = info.available.map((b) => checks?.[`${primitive}/${b}`]);
  const hasChecks = statuses.some((s) => s !== undefined);
  const anyDown = hasChecks && statuses.some((s) => s === "down");
  const allOk = hasChecks && statuses.every((s) => s === "ok");

  let borderClass = "border-gray-200 dark:border-gray-800";
  if (hasChecks) {
    if (allOk) {
      borderClass = "border-green-400 dark:border-green-600";
    } else if (anyDown) {
      borderClass = "border-red-400 dark:border-red-600";
    } else {
      // All reachable (no down, not all ok)
      borderClass = "border-yellow-400 dark:border-yellow-600";
    }
  }

  return (
    <div className={`rounded-lg border ${borderClass} p-4`}>
      <h3 className="text-sm font-semibold font-mono text-gray-900 dark:text-gray-100">
        {primitive}
      </h3>
      <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
        default:{" "}
        <span className="font-mono text-indigo-600 dark:text-indigo-400">
          {info.default}
        </span>
      </p>
      <div className="mt-2 flex flex-wrap gap-1">
        {info.available.map((backend) => {
          const checkKey = `${primitive}/${backend}`;
          const status = checks?.[checkKey];
          const { bg, dot } = statusStyle(status);
          return (
            <span
              key={backend}
              className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-mono ${bg}`}
            >
              {status !== undefined && (
                <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
              )}
              {backend}
            </span>
          );
        })}
      </div>
      {info.available.some((b) => checks?.[`${primitive}/${b}`] === "reachable") && (
        <p className="mt-1.5 text-[10px] text-yellow-600 dark:text-yellow-400">
          Needs user credentials
        </p>
      )}
    </div>
  );
}

function statusStyle(status: string | undefined) {
  switch (status) {
    case "ok":
      return {
        bg: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
        dot: "bg-green-500",
        label: "Healthy",
      };
    case "reachable":
      return {
        bg: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300",
        dot: "bg-yellow-500",
        label: "Reachable — needs user credentials",
      };
    case "down":
      return {
        bg: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
        dot: "bg-red-500",
        label: "Down",
      };
    default:
      return {
        bg: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
        dot: "",
        label: "Unknown",
      };
  }
}
