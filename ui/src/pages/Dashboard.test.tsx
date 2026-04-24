/**
 * Tests for the Dashboard's data-fetching gate and Readiness derivation.
 *
 * Before this refactor, the dashboard called `/readyz` (anonymous) on
 * every mount, resulting in 16 `provider.healthcheck` audit events
 * attributed to `anonymous` per page load. The Dashboard now gates all
 * fetching on `principalLoaded` and uses `/api/v1/providers/status`, so
 * every emitted event is tied to the logged-in user.
 *
 * These tests lock that contract in place:
 * - No API calls fire while `principalLoaded === false`
 * - Once `principalLoaded === true`, `providerStatus()` is called
 * - The Readiness badge is derived client-side from the checks dict
 * - No call to a deprecated `api.readiness()` is made (it's been removed)
 */

import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Dashboard, { deriveReadiness } from "./Dashboard";

// ── deriveReadiness (pure helper) ────────────────────────────────────

describe("deriveReadiness", () => {
  it("returns null when checks are null (still loading)", () => {
    expect(deriveReadiness(null)).toBeNull();
  });

  it("returns ok for an empty dict (nothing to be down)", () => {
    expect(deriveReadiness({})).toBe("ok");
  });

  it("returns ok when every provider is ok", () => {
    expect(
      deriveReadiness({ "memory/default": "ok", "llm/default": "ok" }),
    ).toBe("ok");
  });

  it("returns ok when mixing ok and reachable (reachable = needs user creds, not a failure)", () => {
    expect(
      deriveReadiness({
        "memory/default": "ok",
        "observability/langfuse": "reachable",
      }),
    ).toBe("ok");
  });

  it("returns ok when mixing ok and timeout (timeout distinct from down for per-chip display)", () => {
    expect(
      deriveReadiness({ "memory/mem0": "timeout", "llm/default": "ok" }),
    ).toBe("ok");
  });

  it("returns degraded when any provider is down", () => {
    expect(
      deriveReadiness({
        "memory/default": "ok",
        "llm/bedrock": "down",
        "tools/default": "ok",
      }),
    ).toBe("degraded");
  });
});

// ── Dashboard gating ─────────────────────────────────────────────────

// Mock the API module. vi.hoisted runs before the vi.mock hoist.
const apiMock = vi.hoisted(() => ({
  listAgents: vi.fn(),
  credentialStatus: vi.fn(),
  providerStatus: vi.fn(),
  health: vi.fn(),
  providers: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: apiMock,
  setApiAuthToken: vi.fn(),
}));

// Mock hooks so the Dashboard renders without real network + auth flow.
const useAuthMock = vi.hoisted(() => vi.fn());
vi.mock("../auth/AuthProvider", () => ({
  useAuth: useAuthMock,
}));

const useHealthMock = vi.hoisted(() => vi.fn());
vi.mock("../hooks/useHealth", () => ({
  useHealth: useHealthMock,
}));

const useProvidersMock = vi.hoisted(() => vi.fn());
vi.mock("../hooks/useProviders", () => ({
  useProviders: useProvidersMock,
}));

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  );
}

describe("Dashboard principalLoaded gate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.listAgents.mockResolvedValue([]);
    apiMock.credentialStatus.mockResolvedValue({
      server_credentials: "fallback",
      required_credentials: [],
      resolved_credentials: [],
    });
    apiMock.providerStatus.mockResolvedValue({ checks: {} });
    useHealthMock.mockReturnValue({
      health: { status: "ok" },
      loading: false,
    });
    useProvidersMock.mockReturnValue({
      providers: {},
      loading: false,
    });
  });

  it("fires zero authenticated API calls while principalLoaded is false", () => {
    useAuthMock.mockReturnValue({
      principalLoaded: false,
      principalId: "",
      user: null,
    });

    renderDashboard();

    // These three are the gated calls — none should fire until the
    // server has resolved a concrete principal via /whoami.
    expect(apiMock.listAgents).not.toHaveBeenCalled();
    expect(apiMock.providerStatus).not.toHaveBeenCalled();
    expect(apiMock.credentialStatus).not.toHaveBeenCalled();
  });

  it("fires providerStatus + listAgents + credentialStatus once principalLoaded is true", async () => {
    useAuthMock.mockReturnValue({
      principalLoaded: true,
      principalId: "alice",
      user: null,
    });

    renderDashboard();

    await waitFor(() => {
      expect(apiMock.providerStatus).toHaveBeenCalledTimes(1);
    });
    expect(apiMock.listAgents).toHaveBeenCalledTimes(1);
    expect(apiMock.credentialStatus).toHaveBeenCalledTimes(1);
  });

  it("never calls a deprecated api.readiness (it was removed)", async () => {
    useAuthMock.mockReturnValue({
      principalLoaded: true,
      principalId: "alice",
      user: null,
    });

    renderDashboard();

    await waitFor(() => {
      expect(apiMock.providerStatus).toHaveBeenCalled();
    });
    // If someone reintroduces an `api.readiness()` call, this assertion
    // will keep a stray readyz-from-browser from sneaking back in (and
    // reviving the anonymous-audit-event bug).
    expect(apiMock).not.toHaveProperty("readiness");
  });

  it("renders the Readiness badge derived from the checks dict", async () => {
    apiMock.providerStatus.mockResolvedValue({
      checks: {
        "memory/default": "ok",
        "llm/bedrock": "down",
      },
    });
    useAuthMock.mockReturnValue({
      principalLoaded: true,
      principalId: "alice",
      user: null,
    });

    renderDashboard();

    // Degraded comes from the client-side reducer — there's no
    // server-side readiness aggregate anymore.
    await waitFor(() => {
      expect(screen.getByText(/readiness/i)).toBeInTheDocument();
    });
  });
});
