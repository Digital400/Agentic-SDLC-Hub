import { describe, expect, it } from "vitest";

import {
  estimatedCostExceedsBudget,
  isOptionSelectable,
  resolveAutoSelection,
  resolveSelection,
  type RuntimeOption,
  type RuntimeSelectionState,
} from "./runtime-options";

function option(overrides: Partial<RuntimeOption> = {}): RuntimeOption {
  return {
    key: "opencode",
    label: "OpenCode",
    availability: "available",
    connectionStatus: "connected",
    requiredCapabilities: [],
    executionLocation: "company-sandbox",
    costOwner: "company",
    estimatedMaxCostUsd: 1,
    requiresApproval: false,
    knownLimitations: [],
    supportsModelSelection: true,
    disabledReason: null,
    isPremium: false,
    ...overrides,
  };
}

function state(overrides: Partial<RuntimeSelectionState> = {}): RuntimeSelectionState {
  return { requestedRuntime: "auto", executionLocation: "company-sandbox", modelSource: "auto", budgetUsd: 10, featureFlagEnabled: true, ...overrides };
}

describe("isOptionSelectable", () => {
  it("is selectable when available and not disabled", () => {
    expect(isOptionSelectable(option())).toBe(true);
  });

  it("is not selectable when disabledReason is set", () => {
    expect(isOptionSelectable(option({ disabledReason: "Not connected" }))).toBe(false);
  });

  it("is not selectable when unavailable even with no disabledReason", () => {
    expect(isOptionSelectable(option({ availability: "unavailable" }))).toBe(false);
  });
});

describe("resolveAutoSelection", () => {
  it("picks the first selectable, non-premium option", () => {
    const options = [
      option({ key: "codex", isPremium: true }),
      option({ key: "opencode", isPremium: false }),
    ];
    expect(resolveAutoSelection(options).runtime).toBe("opencode");
  });

  it("falls back to a premium option if nothing non-premium is selectable", () => {
    const options = [option({ key: "codex", isPremium: true })];
    expect(resolveAutoSelection(options).runtime).toBe("codex");
  });

  it("returns 'auto' with an explanatory reason when nothing is selectable", () => {
    const options = [option({ disabledReason: "offline" })];
    const result = resolveAutoSelection(options);
    expect(result.runtime).toBe("auto");
    expect(result.reason).toContain("No runtime is currently available");
  });
});

describe("resolveSelection", () => {
  it("returns the flag-disabled reason and never selects a runtime when the feature flag is off", () => {
    const result = resolveSelection(state({ featureFlagEnabled: false, requestedRuntime: "opencode" }), [option()]);
    expect(result.actualRuntime).toBe("opencode");
    expect(result.requiresConfirmation).toBe(false);
    expect(result.reason).toContain("disabled for this deployment");
  });

  it("resolves 'auto' through resolveAutoSelection", () => {
    const options = [option({ key: "opencode" })];
    const result = resolveSelection(state({ requestedRuntime: "auto" }), options);
    expect(result.actualRuntime).toBe("opencode");
    expect(result.requestedRuntime).toBe("auto");
  });

  it("uses the explicitly requested runtime when it is selectable", () => {
    const options = [option({ key: "opencode" })];
    const result = resolveSelection(state({ requestedRuntime: "opencode" }), options);
    expect(result.actualRuntime).toBe("opencode");
    expect(result.reason).toContain("explicitly requested");
  });

  it("falls back to auto when the explicitly requested runtime is disabled", () => {
    const options = [
      option({ key: "codex", disabledReason: "No connection", isPremium: true }),
      option({ key: "opencode" }),
    ];
    const result = resolveSelection(state({ requestedRuntime: "codex" }), options);
    expect(result.actualRuntime).toBe("opencode");
    expect(result.reason).toContain("unavailable");
  });

  it("requires confirmation when the actual runtime is premium", () => {
    const options = [option({ key: "codex", isPremium: true })];
    const result = resolveSelection(state({ requestedRuntime: "codex" }), options);
    expect(result.requiresConfirmation).toBe(true);
  });

  it("does not require confirmation for a non-premium runtime", () => {
    const options = [option({ key: "opencode", isPremium: false })];
    const result = resolveSelection(state({ requestedRuntime: "opencode" }), options);
    expect(result.requiresConfirmation).toBe(false);
  });
});

describe("estimatedCostExceedsBudget", () => {
  it("is true when the estimate exceeds the budget", () => {
    expect(estimatedCostExceedsBudget(option({ estimatedMaxCostUsd: 20 }), 10)).toBe(true);
  });

  it("is false when the estimate is within budget", () => {
    expect(estimatedCostExceedsBudget(option({ estimatedMaxCostUsd: 5 }), 10)).toBe(false);
  });

  it("is false when the estimate is unknown", () => {
    expect(estimatedCostExceedsBudget(option({ estimatedMaxCostUsd: null }), 10)).toBe(false);
  });
});
