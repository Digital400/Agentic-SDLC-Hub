/**
 * Phase 14: runtime/model selection domain types and pure logic.
 *
 * Kept dependency-free (no React, no fetch) so every rule here — what's
 * disabled and why, whether a confirmation is required, what "Auto"
 * currently resolves to — is unit-testable without rendering anything or
 * mocking a network call. The presentational component
 * (runtime-model-selector.tsx) only renders what this module computes.
 */

export type RuntimeKey = "auto" | "opencode" | "codex" | "claude-code" | "antigravity" | "local-agent";

export type ExecutionLocation = "company-sandbox" | "developer-machine";

export type ModelSource = "auto" | "company-gateway" | "openrouter" | "company-local-model" | "runtime-managed";

export type CostOwner = "company" | "user" | "local-infrastructure";

export type ConnectionStatus = "connected" | "disconnected" | "denied" | "timeout" | "unknown";

export interface RuntimeOption {
  key: RuntimeKey;
  label: string;
  availability: "available" | "unavailable" | "unknown";
  connectionStatus: ConnectionStatus;
  requiredCapabilities: string[];
  executionLocation: ExecutionLocation;
  costOwner: CostOwner;
  estimatedMaxCostUsd: number | null;
  requiresApproval: boolean;
  knownLimitations: string[];
  /** Whether this runtime's own model source can be selected at all (Phase 14: "show model source only when the runtime supports model selection"). */
  supportsModelSelection: boolean;
  /** A human explanation for why this option is disabled, or null if it's selectable. */
  disabledReason: string | null;
  /** True for a company-paid, non-default runtime — gates the required confirmation step. */
  isPremium: boolean;
}

export interface RuntimeSelectionState {
  requestedRuntime: RuntimeKey;
  executionLocation: ExecutionLocation;
  modelSource: ModelSource;
  budgetUsd: number;
  featureFlagEnabled: boolean;
}

export interface RuntimeSelectionResolution {
  requestedRuntime: RuntimeKey;
  /** The runtime that will actually run — may differ from requested (e.g. "auto" resolves to a concrete key, or a disabled choice falls back). */
  actualRuntime: RuntimeKey;
  reason: string;
  requiresConfirmation: boolean;
}

export function isOptionSelectable(option: RuntimeOption): boolean {
  return option.disabledReason === null && option.availability === "available";
}

/**
 * "Auto" selection reason, disclosed as a placeholder: Phase 17's
 * RuntimeCostRouter (not built yet) is the real cost-aware decision
 * engine this is supposed to defer to. Until then, Auto deterministically
 * picks the first selectable, non-premium option in the given list —
 * simple, honest, and replaceable by a single call-site swap once Phase
 * 17 exists, rather than fabricating a "smart" reason here.
 */
export function resolveAutoSelection(options: RuntimeOption[]): { runtime: RuntimeKey; reason: string } {
  const nonPremiumSelectable = options.find((o) => isOptionSelectable(o) && !o.isPremium);
  if (nonPremiumSelectable) {
    return {
      runtime: nonPremiumSelectable.key,
      reason: `Auto selected ${nonPremiumSelectable.label}: the first available, non-premium option (placeholder policy — see Phase 17 RuntimeCostRouter).`,
    };
  }
  const anySelectable = options.find(isOptionSelectable);
  if (anySelectable) {
    return {
      runtime: anySelectable.key,
      reason: `Auto selected ${anySelectable.label}: no non-premium option is currently available.`,
    };
  }
  return { runtime: "auto", reason: "No runtime is currently available; Auto could not resolve a selection." };
}

export function resolveSelection(state: RuntimeSelectionState, options: RuntimeOption[]): RuntimeSelectionResolution {
  if (!state.featureFlagEnabled) {
    return {
      requestedRuntime: state.requestedRuntime,
      actualRuntime: state.requestedRuntime,
      reason: "Runtime/model selection is disabled for this deployment (feature flag off).",
      requiresConfirmation: false,
    };
  }

  if (state.requestedRuntime === "auto") {
    const { runtime, reason } = resolveAutoSelection(options);
    const chosen = options.find((o) => o.key === runtime);
    return {
      requestedRuntime: "auto",
      actualRuntime: runtime,
      reason,
      requiresConfirmation: chosen?.isPremium ?? false,
    };
  }

  const requested = options.find((o) => o.key === state.requestedRuntime);
  if (!requested) {
    return {
      requestedRuntime: state.requestedRuntime,
      actualRuntime: state.requestedRuntime,
      reason: "Unknown runtime requested.",
      requiresConfirmation: false,
    };
  }

  if (!isOptionSelectable(requested)) {
    const { runtime, reason } = resolveAutoSelection(options);
    return {
      requestedRuntime: state.requestedRuntime,
      actualRuntime: runtime,
      reason: `${requested.label} is unavailable (${requested.disabledReason ?? "not selectable"}); falling back — ${reason}`,
      requiresConfirmation: options.find((o) => o.key === runtime)?.isPremium ?? false,
    };
  }

  return {
    requestedRuntime: state.requestedRuntime,
    actualRuntime: state.requestedRuntime,
    reason: `Using the explicitly requested runtime: ${requested.label}.`,
    requiresConfirmation: requested.isPremium,
  };
}

export function estimatedCostExceedsBudget(option: RuntimeOption, budgetUsd: number): boolean {
  return option.estimatedMaxCostUsd !== null && option.estimatedMaxCostUsd > budgetUsd;
}
