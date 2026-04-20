import { useAuth } from "../auth/AuthProvider";

/**
 * Gates a route behind the ``is_admin`` claim reported by
 * ``GET /api/v1/auth/whoami``.  Renders a small "admin required" panel
 * for regular users.  Waits for the whoami response before deciding —
 * the alternative (optimistic render) briefly flashes the protected
 * page to non-admins before kicking them out.
 */
export default function AdminGuard({
  children,
}: {
  children: React.ReactNode;
}) {
  const { principalLoaded, isAdmin } = useAuth();

  if (!principalLoaded) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-sm text-gray-500 dark:text-gray-400">
        <div className="flex items-center gap-3">
          <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-gray-400 border-t-transparent" />
          Checking permissions…
        </div>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="mx-auto max-w-xl p-8 text-center">
        <div className="rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-6">
          <h2 className="text-lg font-semibold text-red-700 dark:text-red-300">
            Admin access required
          </h2>
          <p className="mt-2 text-sm text-red-600/80 dark:text-red-300/80">
            This page is restricted to operators with the <code>admin</code>{" "}
            scope.
          </p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
