import type { ProviderInfo } from "../api/types";

export default function ProviderCard({
  primitive,
  info,
  checks,
}: {
  primitive: string;
  info: ProviderInfo;
  checks?: Record<string, boolean>;
}) {
  // Determine overall health for this primitive's providers
  const healthStatuses = info.available.map(
    (b) => checks?.[`${primitive}/${b}`],
  );
  const hasChecks = healthStatuses.some((s) => s !== undefined);
  const allHealthy = hasChecks && healthStatuses.every((s) => s === true);
  const allUnhealthy = hasChecks && healthStatuses.every((s) => s === false);

  let borderClass = "border-gray-200 dark:border-gray-800";
  if (hasChecks) {
    if (allHealthy) {
      borderClass = "border-green-400 dark:border-green-600";
    } else if (allUnhealthy) {
      borderClass = "border-red-400 dark:border-red-600";
    } else {
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
          const healthy = checks?.[checkKey];
          return (
            <span
              key={backend}
              className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-mono ${
                healthy === true
                  ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300"
                  : healthy === false
                    ? "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300"
                    : "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300"
              }`}
            >
              {healthy !== undefined && (
                <span
                  className={`h-1.5 w-1.5 rounded-full ${healthy ? "bg-green-500" : "bg-red-500"}`}
                />
              )}
              {backend}
            </span>
          );
        })}
      </div>
    </div>
  );
}
