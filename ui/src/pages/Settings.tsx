import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

const APG_PREFIX = "apg.";

interface CredentialStatus {
  source: string;
  aws_configured: boolean;
  aws_credential_expiry: string | null;
}

interface MaskedCredentials {
  attributes: Record<string, string>;
}

interface CredentialRow {
  key: string;
  value: string;
}

export default function Settings() {
  const [status, setStatus] = useState<CredentialStatus | null>(null);
  const [credentials, setCredentials] = useState<MaskedCredentials | null>(null);
  const [newRows, setNewRows] = useState<CredentialRow[]>([]);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [statusRes, credsRes] = await Promise.allSettled([
        api.credentialStatus(),
        api.readCredentials(),
      ]);
      if (statusRes.status === "fulfilled") setStatus(statusRes.value);
      if (credsRes.status === "fulfilled") setCredentials(credsRes.value);
    } catch {
      // endpoints may not be configured
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSave = async () => {
    const attrs: Record<string, string> = {};
    for (const row of newRows) {
      const k = row.key.trim();
      const v = row.value.trim();
      if (!k || !v) continue;
      const fullKey = k.startsWith(APG_PREFIX) ? k : `${APG_PREFIX}${k}`;
      attrs[fullKey] = v;
    }
    if (Object.keys(attrs).length === 0) return;

    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      await api.writeCredentials({ attributes: attrs });
      setSuccess("Credentials saved");
      setNewRows([]);
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save credentials");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (fullKey: string) => {
    setDeleting(fullKey);
    setError(null);
    setSuccess(null);
    try {
      await api.deleteCredential(fullKey);
      setSuccess(`Deleted ${fullKey}`);
      await loadData();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete credential");
    } finally {
      setDeleting(null);
    }
  };

  const addRow = () => setNewRows((prev) => [...prev, { key: "", value: "" }]);
  const updateRow = (i: number, field: "key" | "value", val: string) =>
    setNewRows((prev) => prev.map((r, j) => (j === i ? { ...r, [field]: val } : r)));
  const removeRow = (i: number) => setNewRows((prev) => prev.filter((_, j) => j !== i));

  const hasNewEdits = newRows.some((r) => r.key.trim() && r.value.trim());

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500 dark:text-gray-400">
        Loading credentials...
      </div>
    );
  }

  // Group existing credentials by service for display
  const grouped: Record<string, { key: string; masked: string; fullKey: string }[]> = {};
  if (credentials) {
    for (const [fullKey, masked] of Object.entries(credentials.attributes)) {
      const rest = fullKey.startsWith(APG_PREFIX) ? fullKey.slice(APG_PREFIX.length) : fullKey;
      const dot = rest.indexOf(".");
      const service = dot > 0 ? rest.slice(0, dot) : "_other";
      const key = dot > 0 ? rest.slice(dot + 1) : rest;
      (grouped[service] ??= []).push({ key, masked, fullKey });
    }
  }
  const hasStored = Object.keys(grouped).length > 0;

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">Settings</h1>

      {/* Status */}
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">
          Credential Resolution
        </h2>
        <div className="flex items-center gap-3">
          <span
            className={`inline-block px-2 py-0.5 rounded text-xs font-mono ${
              status?.source === "oidc"
                ? "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300"
                : status?.source === "none"
                  ? "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400"
                  : "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300"
            }`}
          >
            {status?.source ?? "unknown"}
          </span>
          {status?.aws_configured && (
            <span className="text-xs text-gray-500 dark:text-gray-400">
              AWS federation enabled
            </span>
          )}
        </div>
      </div>

      {/* Stored credentials */}
      {hasStored && (
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4">
          <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">
            Stored Credentials
          </h2>
          <div className="space-y-3">
            {Object.entries(grouped).map(([service, items]) => (
              <div key={service}>
                <h3 className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase mb-1.5">
                  {service}
                </h3>
                <div className="space-y-1.5">
                  {items.map(({ key, masked, fullKey }) => (
                    <div key={fullKey} className="flex items-center justify-between group">
                      <span className="text-xs font-mono text-gray-600 dark:text-gray-400">
                        {key}
                      </span>
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono text-gray-400 dark:text-gray-500">
                          {masked}
                        </span>
                        <button
                          onClick={() => handleDelete(fullKey)}
                          disabled={deleting === fullKey}
                          className="text-[10px] text-gray-400 hover:text-red-500 dark:hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                          title={`Delete ${fullKey}`}
                        >
                          {deleting === fullKey ? "..." : "delete"}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Add new credentials */}
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
            Add Credentials
          </h2>
          <button
            onClick={addRow}
            className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:text-indigo-800 dark:hover:text-indigo-300"
          >
            + Add
          </button>
        </div>

        <div className="mb-4 rounded bg-gray-50 dark:bg-gray-750 border border-gray-200 dark:border-gray-700 px-3 py-2">
          <p className="text-xs text-gray-600 dark:text-gray-400 leading-relaxed">
            <span className="font-semibold">Naming convention:</span> use{" "}
            <code className="text-[11px] bg-gray-200 dark:bg-gray-700 px-1 rounded">
              service.key
            </code>{" "}
            format. The{" "}
            <code className="text-[11px] bg-gray-200 dark:bg-gray-700 px-1 rounded">apg.</code>{" "}
            prefix is added automatically.
          </p>
          <p className="text-[11px] text-gray-500 dark:text-gray-500 mt-1.5 font-mono leading-relaxed">
            langfuse.public_key <span className="text-gray-400">{"→"}</span>{" "}
            service_credentials["langfuse"]["public_key"]
            <br />
            mcp_registry.api_key <span className="text-gray-400">{"→"}</span>{" "}
            service_credentials["mcp_registry"]["api_key"]
          </p>
        </div>

        {newRows.length === 0 && (
          <p className="text-xs text-gray-400 dark:text-gray-500 italic">
            Click "+ Add" to store a new credential.
          </p>
        )}

        <div className="space-y-2">
          {newRows.map((row, i) => (
            <div key={i} className="flex items-center gap-2">
              <div className="flex items-center gap-0 shrink-0">
                <span className="text-xs font-mono text-gray-400 dark:text-gray-500 select-none">
                  apg.
                </span>
                <input
                  type="text"
                  value={row.key}
                  onChange={(e) => updateRow(i, "key", e.target.value)}
                  placeholder="service.key_name"
                  className="w-44 px-1.5 py-1 text-xs font-mono rounded-l border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>
              <input
                type="text"
                value={row.value}
                onChange={(e) => updateRow(i, "value", e.target.value)}
                placeholder="value"
                className="flex-1 px-2 py-1 text-xs font-mono rounded-r border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
              <button
                onClick={() => removeRow(i)}
                className="text-xs text-gray-400 hover:text-red-500 dark:hover:text-red-400 shrink-0 px-1"
              >
                x
              </button>
            </div>
          ))}
        </div>

        {hasNewEdits && (
          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-3 py-1.5 text-sm font-medium rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save"}
            </button>
            <button
              onClick={() => setNewRows([])}
              className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
            >
              Cancel
            </button>
          </div>
        )}

        {error && <p className="mt-2 text-xs text-red-600 dark:text-red-400">{error}</p>}
        {success && <p className="mt-2 text-xs text-green-600 dark:text-green-400">{success}</p>}
      </div>
    </div>
  );
}
