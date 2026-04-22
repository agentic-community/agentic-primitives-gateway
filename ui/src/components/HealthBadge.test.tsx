/**
 * Smoke tests for HealthBadge — the component signals gateway
 * status in the dashboard.  Contract: the label text appears,
 * and the color class reflects the status.  A regression where
 * the label was swapped to a different prop (or the status
 * mapping silently broke) would show wrong health to operators.
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import HealthBadge from "./HealthBadge";

describe("HealthBadge", () => {
  it("renders the label text", () => {
    render(<HealthBadge status="ok" label="All systems operational" />);
    expect(screen.getByText("All systems operational")).toBeInTheDocument();
  });

  it("uses green styling for ok status", () => {
    const { container } = render(<HealthBadge status="ok" label="Healthy" />);
    const span = container.querySelector("span");
    expect(span?.className).toMatch(/green/);
  });

  it("uses yellow styling for degraded status", () => {
    const { container } = render(<HealthBadge status="degraded" label="Slow" />);
    const span = container.querySelector("span");
    expect(span?.className).toMatch(/yellow/);
  });

  it("uses red styling for error status", () => {
    const { container } = render(<HealthBadge status="error" label="Down" />);
    const span = container.querySelector("span");
    expect(span?.className).toMatch(/red/);
  });
});
