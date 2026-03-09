import { useCallback, useEffect, useState } from "react";
import type { ProvidersResponse } from "../api/types";
import LoadingSpinner from "../components/LoadingSpinner";

interface RouteInfo {
  path: string;
  method: string;
  name: string;
  summary: string;
  pathParams: string[];
  hasBody: boolean;
}

interface GroupedRoutes {
  [primitive: string]: RouteInfo[];
}

function extractRoutes(spec: Record<string, unknown>): GroupedRoutes {
  const paths = (spec.paths ?? {}) as Record<string, Record<string, unknown>>;
  const grouped: GroupedRoutes = {};

  for (const [path, methods] of Object.entries(paths)) {
    if (!path.startsWith("/api/v1/") || path.startsWith("/api/v1/providers"))
      continue;

    const segment = path.replace("/api/v1/", "").split("/")[0];
    const primitive = segment.replace(/-/g, "_");

    for (const [method, details] of Object.entries(methods)) {
      if (["get", "post", "put", "delete", "patch"].indexOf(method) === -1)
        continue;
      const info = details as Record<string, unknown>;
      const params = (info.parameters ?? []) as Array<Record<string, unknown>>;
      const pathParams = params
        .filter((p) => p.in === "path")
        .map((p) => p.name as string);
      const hasBody = !!info.requestBody;

      if (!grouped[primitive]) grouped[primitive] = [];
      grouped[primitive].push({
        path,
        method: method.toUpperCase(),
        name: (info.summary as string) ?? (info.operationId as string) ?? "",
        summary:
          (info.summary as string) ?? (info.operationId as string) ?? path,
        pathParams,
        hasBody,
      });
    }
  }
  return grouped;
}

const METHOD_COLORS: Record<string, string> = {
  GET: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  POST: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  PUT: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300",
  DELETE: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300",
  PATCH:
    "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300",
};

function RouteCard({
  route,
  headers,
}: {
  route: RouteInfo;
  headers: Record<string, string>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [body, setBody] = useState("{}");
  const [response, setResponse] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  const execute = useCallback(async () => {
    setLoading(true);
    setResponse(null);
    setStatus(null);
    try {
      let url = route.path;
      for (const param of route.pathParams) {
        url = url.replace(
          `{${param}}`,
          encodeURIComponent(paramValues[param] || ""),
        );
        url = url.replace(
          `{${param}:path}`,
          encodeURIComponent(paramValues[param] || ""),
        );
      }

      const init: RequestInit = {
        method: route.method,
        headers: {
          "Content-Type": "application/json",
          ...headers,
        },
      };

      if (
        route.hasBody &&
        route.method !== "GET" &&
        route.method !== "DELETE"
      ) {
        init.body = body;
      }

      const res = await fetch(url, init);
      setStatus(res.status);
      const text = await res.text();
      try {
        setResponse(JSON.stringify(JSON.parse(text), null, 2));
      } catch {
        setResponse(text);
      }
    } catch (err) {
      setResponse(err instanceof Error ? err.message : "Request failed");
      setStatus(0);
    } finally {
      setLoading(false);
    }
  }, [route, paramValues, body, headers]);

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-50 dark:hover:bg-gray-800/50"
      >
        <span
          className={`inline-flex items-center justify-center rounded px-1.5 py-0.5 text-[10px] font-bold w-14 text-center ${METHOD_COLORS[route.method] || ""}`}
        >
          {route.method}
        </span>
        <span className="font-mono text-xs text-gray-700 dark:text-gray-300 flex-1 truncate">
          {route.path}
        </span>
        <span
          className={`text-[10px] transition-transform ${expanded ? "rotate-90" : ""}`}
        >
          &#9654;
        </span>
      </button>

      {expanded && (
        <div className="border-t border-gray-200 dark:border-gray-700 p-3 space-y-3">
          {/* Path params */}
          {route.pathParams.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-[10px] font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
                Path Parameters
              </p>
              {route.pathParams.map((param) => (
                <div key={param} className="flex items-center gap-2">
                  <label className="text-xs font-mono text-gray-600 dark:text-gray-400 w-32 shrink-0">
                    {param}
                  </label>
                  <input
                    value={paramValues[param] || ""}
                    onChange={(e) =>
                      setParamValues((prev) => ({
                        ...prev,
                        [param]: e.target.value,
                      }))
                    }
                    placeholder={param}
                    className="flex-1 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs font-mono"
                  />
                </div>
              ))}
            </div>
          )}

          {/* Request body */}
          {route.hasBody &&
            route.method !== "GET" &&
            route.method !== "DELETE" && (
              <div className="space-y-1.5">
                <p className="text-[10px] font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
                  Request Body (JSON)
                </p>
                <textarea
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  rows={4}
                  className="w-full rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-1.5 text-xs font-mono"
                />
              </div>
            )}

          {/* Execute */}
          <button
            onClick={execute}
            disabled={loading}
            className="rounded bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {loading ? "Sending..." : "Send Request"}
          </button>

          {/* Response */}
          {response !== null && (
            <div className="space-y-1">
              <p className="text-[10px] font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400">
                Response{" "}
                {status !== null && (
                  <span
                    className={
                      status >= 200 && status < 300
                        ? "text-green-600 dark:text-green-400"
                        : "text-red-600 dark:text-red-400"
                    }
                  >
                    {status}
                  </span>
                )}
              </p>
              <pre className="rounded bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 p-2.5 text-xs font-mono text-gray-800 dark:text-gray-200 overflow-x-auto max-h-64 overflow-y-auto whitespace-pre-wrap">
                {response}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function PrimitiveExplorer() {
  const [spec, setSpec] = useState<Record<string, unknown> | null>(null);
  const [providers, setProviders] = useState<ProvidersResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedPrimitive, setSelectedPrimitive] = useState<string | null>(
    null,
  );
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [headers, setHeaders] = useState<Record<string, string>>({});
  const [headerKey, setHeaderKey] = useState("");
  const [headerValue, setHeaderValue] = useState("");

  useEffect(() => {
    Promise.all([
      fetch("/api/v1/openapi").then((r) => r.json()),
      fetch("/api/v1/providers").then((r) => r.json()),
    ])
      .then(([s, p]) => {
        setSpec(s);
        setProviders(p);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const addHeader = useCallback(() => {
    if (!headerKey.trim()) return;
    setHeaders((prev) => ({ ...prev, [headerKey.trim()]: headerValue }));
    setHeaderKey("");
    setHeaderValue("");
  }, [headerKey, headerValue]);

  // When switching primitives, reset provider to the default for that primitive
  const handleSelectPrimitive = useCallback(
    (prim: string) => {
      setSelectedPrimitive(prim);
      const providerInfo = providers?.[prim];
      setSelectedProvider(providerInfo?.default ?? null);
    },
    [providers],
  );

  if (loading || !spec) return <LoadingSpinner className="mt-32" />;

  const grouped = extractRoutes(spec);
  const primitives = Object.keys(grouped).sort();
  const routes = selectedPrimitive ? grouped[selectedPrimitive] ?? [] : [];

  // Build effective headers: merge custom headers with the provider override
  const providerInfo = selectedPrimitive
    ? providers?.[selectedPrimitive]
    : null;
  const effectiveHeaders = { ...headers };
  if (selectedPrimitive && selectedProvider) {
    // X-Provider-Memory, X-Provider-Code-Interpreter, etc.
    const headerName = `X-Provider-${selectedPrimitive.replace(/_/g, "-")}`;
    effectiveHeaders[headerName] = selectedProvider;
  }

  return (
    <div className="max-w-5xl space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          Primitives Explorer
        </h1>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
          Select a primitive and provider, pick an endpoint, fill parameters,
          and execute.
        </p>
      </div>

      {/* Custom headers */}
      <details className="rounded-lg border border-gray-200 dark:border-gray-800">
        <summary className="px-4 py-2 text-xs font-medium text-gray-500 dark:text-gray-400 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50">
          Custom Headers ({Object.keys(headers).length})
          <span className="text-[10px] text-gray-400 dark:text-gray-500 ml-2">
            e.g. X-Agent-Id, X-AWS-Access-Key-Id
          </span>
        </summary>
        <div className="px-4 pb-3 space-y-2 border-t border-gray-200 dark:border-gray-700 pt-2">
          {Object.entries(headers).map(([k, v]) => (
            <div
              key={k}
              className="flex items-center gap-2 text-xs font-mono"
            >
              <span className="text-gray-600 dark:text-gray-400">{k}:</span>
              <span className="text-gray-800 dark:text-gray-200">{v}</span>
              <button
                onClick={() =>
                  setHeaders((prev) => {
                    const copy = { ...prev };
                    delete copy[k];
                    return copy;
                  })
                }
                className="text-red-500 hover:text-red-700 text-[10px]"
              >
                remove
              </button>
            </div>
          ))}
          <div className="flex gap-2">
            <input
              placeholder="Header name"
              value={headerKey}
              onChange={(e) => setHeaderKey(e.target.value)}
              className="flex-1 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs font-mono"
            />
            <input
              placeholder="Value"
              value={headerValue}
              onChange={(e) => setHeaderValue(e.target.value)}
              className="flex-1 rounded border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs font-mono"
            />
            <button
              onClick={addHeader}
              disabled={!headerKey.trim()}
              className="rounded bg-gray-200 dark:bg-gray-700 px-2 py-1 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-300 dark:hover:bg-gray-600 disabled:opacity-50"
            >
              Add
            </button>
          </div>
        </div>
      </details>

      <div className="flex gap-4">
        {/* Primitive list */}
        <div className="w-48 shrink-0 space-y-1">
          {primitives.map((prim) => {
            const info = providers?.[prim];
            return (
              <button
                key={prim}
                onClick={() => handleSelectPrimitive(prim)}
                className={`w-full text-left rounded px-3 py-1.5 text-sm font-mono ${
                  selectedPrimitive === prim
                    ? "bg-indigo-100 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300 font-medium"
                    : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                }`}
              >
                <span>{prim}</span>
                <span className="text-[10px] text-gray-400 dark:text-gray-500 ml-1">
                  ({grouped[prim].length})
                </span>
                {info && (
                  <span className="block text-[10px] text-gray-400 dark:text-gray-500 font-normal">
                    {info.available.length} provider
                    {info.available.length !== 1 ? "s" : ""}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Routes */}
        <div className="flex-1 space-y-2">
          {!selectedPrimitive ? (
            <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
              Select a primitive to see its endpoints.
            </p>
          ) : (
            <>
              {/* Provider selector */}
              {providerInfo && providerInfo.available.length > 0 && (
                <div className="flex items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-800 px-3 py-2">
                  <span className="text-[10px] font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400 shrink-0">
                    Provider
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {providerInfo.available.map((name) => (
                      <button
                        key={name}
                        onClick={() => setSelectedProvider(name)}
                        className={`rounded px-2 py-0.5 text-xs font-mono transition-colors ${
                          selectedProvider === name
                            ? "bg-indigo-600 text-white"
                            : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700"
                        }`}
                      >
                        {name}
                        {name === providerInfo.default && (
                          <span className="ml-1 text-[9px] opacity-60">
                            default
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {routes.length === 0 ? (
                <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
                  No routes found for {selectedPrimitive}.
                </p>
              ) : (
                routes.map((route) => (
                  <RouteCard
                    key={`${route.method}:${route.path}`}
                    route={route}
                    headers={effectiveHeaders}
                  />
                ))
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
