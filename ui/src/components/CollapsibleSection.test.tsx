/**
 * Smoke tests for CollapsibleSection — the primary disclosure
 * widget used across the chat (tool call blocks, sub-agent blocks,
 * artifacts).  Contract:
 * - children hidden when collapsed, visible when expanded
 * - click toggles the state
 * - aria-expanded reflects state
 * - defaultOpen controls initial state
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect } from "vitest";
import CollapsibleSection from "./CollapsibleSection";

describe("CollapsibleSection", () => {
  it("hides children by default", () => {
    render(
      <CollapsibleSection header="Details">
        <p>hidden content</p>
      </CollapsibleSection>,
    );
    expect(screen.queryByText("hidden content")).not.toBeInTheDocument();
  });

  it("shows children when defaultOpen is true", () => {
    render(
      <CollapsibleSection header="Details" defaultOpen>
        <p>visible content</p>
      </CollapsibleSection>,
    );
    expect(screen.getByText("visible content")).toBeInTheDocument();
  });

  it("toggles children on header click", async () => {
    const user = userEvent.setup();
    render(
      <CollapsibleSection header="Click me">
        <p>toggleable content</p>
      </CollapsibleSection>,
    );
    expect(screen.queryByText("toggleable content")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button"));
    expect(screen.getByText("toggleable content")).toBeInTheDocument();

    await user.click(screen.getByRole("button"));
    expect(screen.queryByText("toggleable content")).not.toBeInTheDocument();
  });

  it("aria-expanded reflects state", async () => {
    const user = userEvent.setup();
    render(
      <CollapsibleSection header="h">
        <p>x</p>
      </CollapsibleSection>,
    );
    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-expanded", "false");

    await user.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
  });
});
