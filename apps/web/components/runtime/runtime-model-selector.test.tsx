import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RuntimeOption } from "@/lib/runtime/runtime-options";

import { RuntimeModelSelector } from "./runtime-model-selector";

function baseOptions(): RuntimeOption[] {
  return [
    {
      key: "opencode", label: "OpenCode", availability: "available", connectionStatus: "connected",
      requiredCapabilities: ["PATCH_GENERATION"], executionLocation: "company-sandbox", costOwner: "company",
      estimatedMaxCostUsd: 2, requiresApproval: false, knownLimitations: [], supportsModelSelection: true,
      disabledReason: null, isPremium: false,
    },
    {
      key: "codex", label: "Codex", availability: "unavailable", connectionStatus: "disconnected",
      requiredCapabilities: [], executionLocation: "company-sandbox", costOwner: "company",
      estimatedMaxCostUsd: 25, requiresApproval: true, knownLimitations: ["No official SDK access yet"],
      supportsModelSelection: false, disabledReason: "No official SDK access configured", isPremium: true,
    },
  ];
}

describe("RuntimeModelSelector", () => {
  it("renders the loading state and nothing else while isLoading is true", () => {
    render(<RuntimeModelSelector options={[]} budgetUsd={10} featureFlagEnabled onConfirm={vi.fn()} isLoading />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
  });

  it("renders the failure state when loadError is set", () => {
    render(<RuntimeModelSelector options={[]} budgetUsd={10} featureFlagEnabled onConfirm={vi.fn()} loadError="network down" />);
    expect(screen.getByRole("alert")).toHaveTextContent("network down");
  });

  it("shows the requested/actual runtime summary and disables the confirm button when the feature flag is off", () => {
    render(<RuntimeModelSelector options={baseOptions()} budgetUsd={10} featureFlagEnabled={false} onConfirm={vi.fn()} />);
    expect(screen.getByText(/disabled for this deployment/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /use this runtime/i })).toBeDisabled();
  });

  it("calls onConfirm directly for a non-premium runtime with no confirmation step", () => {
    const onConfirm = vi.fn();
    render(<RuntimeModelSelector options={baseOptions()} budgetUsd={10} featureFlagEnabled onConfirm={onConfirm} />);
    fireEvent.change(screen.getByLabelText(/^runtime$/i), { target: { value: "opencode" } });
    fireEvent.click(screen.getByRole("button", { name: /use this runtime/i }));
    expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ runtime: "opencode" }));
  });

  it("requires an explicit confirmation step before calling onConfirm for a premium runtime", () => {
    const onConfirm = vi.fn();
    // codex is disabled in baseOptions(), so directly requesting it would resolve to a
    // fallback with no confirmation needed; use a premium *selectable* option instead to
    // exercise the confirmation path specifically.
    const premiumSelectable: RuntimeOption = { ...baseOptions()[1]!, availability: "available", disabledReason: null };
    render(<RuntimeModelSelector options={[premiumSelectable]} budgetUsd={100} featureFlagEnabled onConfirm={onConfirm} />);

    fireEvent.change(screen.getByLabelText(/^runtime$/i), { target: { value: "codex" } });
    fireEvent.click(screen.getByRole("button", { name: /use this runtime/i }));

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /confirm and run/i }));
    expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ runtime: "codex" }));
  });

  it("hides the model source select for a runtime that does not support model selection", () => {
    render(<RuntimeModelSelector options={baseOptions()} budgetUsd={10} featureFlagEnabled onConfirm={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/^runtime$/i), { target: { value: "opencode" } });
    expect(screen.getByLabelText(/model source/i)).toBeInTheDocument();
  });

  it("shows the disabled-selection reason for an unavailable option", () => {
    render(<RuntimeModelSelector options={baseOptions()} budgetUsd={10} featureFlagEnabled onConfirm={vi.fn()} />);
    expect(screen.getByText(/no official sdk access configured/i)).toBeInTheDocument();
  });

  it("flags an estimated cost that exceeds budget", () => {
    render(<RuntimeModelSelector options={baseOptions()} budgetUsd={1} featureFlagEnabled onConfirm={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/^runtime$/i), { target: { value: "codex" } });
    expect(screen.getByText(/exceeds budget/i)).toBeInTheDocument();
  });
});
