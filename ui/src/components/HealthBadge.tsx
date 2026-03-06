import { cn } from "../lib/cn";

const statusColors = {
  ok: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400",
  degraded:
    "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-400",
  error: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400",
};

export default function HealthBadge({
  status,
  label,
}: {
  status: "ok" | "degraded" | "error";
  label: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium",
        statusColors[status],
      )}
    >
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          status === "ok" && "bg-green-500",
          status === "degraded" && "bg-yellow-500",
          status === "error" && "bg-red-500",
        )}
      />
      {label}
    </span>
  );
}
