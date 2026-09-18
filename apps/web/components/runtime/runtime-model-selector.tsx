"use client";

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import {
  estimatedCostExceedsBudget,
  isOptionSelectable,
  resolveSelection,
  type ExecutionLocation,
  type ModelSource,
  type RuntimeKey,
  type RuntimeOption,
} from "@/lib/runtime/runtime-options";

export interface RuntimeModelSelectorProps {
  options: RuntimeOption[];
  budgetUsd: number;
  featureFlagEnabled: boolean;
  /** True while options are still being fetched — renders the loading state. */
  isLoading?: boolean;
  /** Set when the options fetch itself failed — renders the failure state. */
  loadError?: string | null;
  onConfirm: (selection: { runtime: RuntimeKey; executionLocation: ExecutionLocation; modelSource: ModelSource }) => void;
}

const RUNTIME_LABELS: Record<RuntimeKey, string> = {
  auto: "Auto — Recommended",
  opencode: "OpenCode",
  codex: "Codex",
  "claude-code": "Claude Code",
  antigravity: "Antigravity",
  "local-agent": "Connected local agent",
};

const EXECUTION_LOCATION_LABELS: Record<ExecutionLocation, string> = {
  "company-sandbox": "Company sandbox",
  "developer-machine": "My connected development machine",
};

const MODEL_SOURCE_LABELS: Record<ModelSource, string> = {
  auto: "Auto",
  "company-gateway": "Company gateway",
  openrouter: "OpenRouter",
  "company-local-model": "Company local model",
  "runtime-managed": "Runtime-managed model",
};

function connectionStatusBadge(status: RuntimeOption["connectionStatus"]) {
  switch (status) {
    case "connected":
      return <Badge variant="success">Connected</Badge>;
    case "disconnected":
      return <Badge variant="gray">Disconnected</Badge>;
    case "denied":
      return <Badge variant="destructive">Access denied</Badge>;
    case "timeout":
      return <Badge variant="warning">Connection timed out</Badge>;
    default:
      return <Badge variant="outline">Unknown</Badge>;
  }
}

export function RuntimeModelSelector({
  options,
  budgetUsd,
  featureFlagEnabled,
  isLoading = false,
  loadError = null,
  onConfirm,
}: RuntimeModelSelectorProps) {
  const [requestedRuntime, setRequestedRuntime] = useState<RuntimeKey>("auto");
  const [executionLocation, setExecutionLocation] = useState<ExecutionLocation>("company-sandbox");
  const [modelSource, setModelSource] = useState<ModelSource>("auto");
  const [pendingPremiumConfirm, setPendingPremiumConfirm] = useState(false);

  const resolution = useMemo(
    () => resolveSelection({ requestedRuntime, executionLocation, modelSource, budgetUsd, featureFlagEnabled }, options),
    [requestedRuntime, executionLocation, modelSource, budgetUsd, featureFlagEnabled, options],
  );

  const selectedOption = options.find((o) => o.key === requestedRuntime);
  const actualOption = options.find((o) => o.key === resolution.actualRuntime);

  if (isLoading) {
    return (
      <Card role="status" aria-live="polite">
        <CardContent className="p-4 text-sm text-muted-foreground">Loading available runtimes…</CardContent>
      </Card>
    );
  }

  if (loadError) {
    return (
      <Card role="alert">
        <CardContent className="p-4 text-sm text-destructive">Could not load runtime options: {loadError}</CardContent>
      </Card>
    );
  }

  function handleConfirmClick() {
    if (actualOption?.isPremium && !pendingPremiumConfirm) {
      setPendingPremiumConfirm(true);
      return;
    }
    setPendingPremiumConfirm(false);
    onConfirm({ runtime: resolution.actualRuntime, executionLocation, modelSource });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Runtime and model</CardTitle>
        <CardDescription>Choose where and how this task runs.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor="runtime-select" className="text-xs font-medium text-muted-foreground">
            Runtime
          </label>
          <Select
            id="runtime-select"
            value={requestedRuntime}
            onChange={(e) => setRequestedRuntime(e.target.value as RuntimeKey)}
          >
            {(Object.keys(RUNTIME_LABELS) as RuntimeKey[]).map((key) => {
              const option = options.find((o) => o.key === key);
              const disabled = key !== "auto" && option ? !isOptionSelectable(option) : false;
              return (
                <option key={key} value={key} disabled={disabled}>
                  {RUNTIME_LABELS[key]}
                  {disabled && option?.disabledReason ? ` (${option.disabledReason})` : ""}
                </option>
              );
            })}
          </Select>
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="execution-location-select" className="text-xs font-medium text-muted-foreground">
            Execution location
          </label>
          <Select
            id="execution-location-select"
            value={executionLocation}
            onChange={(e) => setExecutionLocation(e.target.value as ExecutionLocation)}
          >
            {(Object.keys(EXECUTION_LOCATION_LABELS) as ExecutionLocation[]).map((key) => (
              <option key={key} value={key}>
                {EXECUTION_LOCATION_LABELS[key]}
              </option>
            ))}
          </Select>
        </div>

        {selectedOption?.supportsModelSelection && (
          <div className="flex flex-col gap-1">
            <label htmlFor="model-source-select" className="text-xs font-medium text-muted-foreground">
              Model source
            </label>
            <Select id="model-source-select" value={modelSource} onChange={(e) => setModelSource(e.target.value as ModelSource)}>
              {(Object.keys(MODEL_SOURCE_LABELS) as ModelSource[]).map((key) => (
                <option key={key} value={key}>
                  {MODEL_SOURCE_LABELS[key]}
                </option>
              ))}
            </Select>
          </div>
        )}

        {selectedOption && (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-md border border-border p-3 text-xs">
            <dt className="text-muted-foreground">Availability</dt>
            <dd>{selectedOption.availability}</dd>
            <dt className="text-muted-foreground">Connection status</dt>
            <dd>{connectionStatusBadge(selectedOption.connectionStatus)}</dd>
            <dt className="text-muted-foreground">Required capabilities</dt>
            <dd>{selectedOption.requiredCapabilities.join(", ") || "None"}</dd>
            <dt className="text-muted-foreground">Execution / data location</dt>
            <dd>{EXECUTION_LOCATION_LABELS[selectedOption.executionLocation]}</dd>
            <dt className="text-muted-foreground">Cost owner</dt>
            <dd>{selectedOption.costOwner}</dd>
            <dt className="text-muted-foreground">Estimated maximum cost</dt>
            <dd className={estimatedCostExceedsBudget(selectedOption, budgetUsd) ? "font-semibold text-destructive" : ""}>
              {selectedOption.estimatedMaxCostUsd === null ? "Unknown" : `$${selectedOption.estimatedMaxCostUsd.toFixed(2)}`}
              {estimatedCostExceedsBudget(selectedOption, budgetUsd) ? " (exceeds budget)" : ""}
            </dd>
            <dt className="text-muted-foreground">Approval requirement</dt>
            <dd>{selectedOption.requiresApproval ? "Required" : "Not required"}</dd>
            <dt className="text-muted-foreground">Known limitations</dt>
            <dd>{selectedOption.knownLimitations.join(", ") || "None known"}</dd>
            {selectedOption.disabledReason && (
              <>
                <dt className="text-muted-foreground">Disabled-selection reason</dt>
                <dd className="text-destructive">{selectedOption.disabledReason}</dd>
              </>
            )}
          </dl>
        )}

        <div className="flex flex-col gap-1 rounded-md bg-muted/50 p-3 text-xs">
          <div>
            <span className="text-muted-foreground">Requested runtime: </span>
            {RUNTIME_LABELS[resolution.requestedRuntime]}
          </div>
          <div>
            <span className="text-muted-foreground">Actual selected runtime: </span>
            {RUNTIME_LABELS[resolution.actualRuntime] ?? resolution.actualRuntime}
          </div>
          <div>
            <span className="text-muted-foreground">Selection reason: </span>
            {resolution.reason}
          </div>
          <div>
            <span className="text-muted-foreground">Budget: </span>${budgetUsd.toFixed(2)}
          </div>
          <div>
            <span className="text-muted-foreground">Feature flag: </span>
            {featureFlagEnabled ? "Enabled" : "Disabled"}
          </div>
        </div>

        {pendingPremiumConfirm && (
          <div role="alertdialog" aria-label="Confirm premium runtime" className="rounded-md border border-amber-400 bg-amber-50 p-3 text-xs dark:bg-amber-950">
            <p className="mb-2">
              {actualOption ? RUNTIME_LABELS[actualOption.key] : "This runtime"} is company-paid. Confirm you want to proceed.
            </p>
            <div className="flex gap-2">
              <Button size="sm" onClick={handleConfirmClick}>
                Confirm and run
              </Button>
              <Button size="sm" variant="outline" onClick={() => setPendingPremiumConfirm(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {!pendingPremiumConfirm && (
          <Button onClick={handleConfirmClick} disabled={!featureFlagEnabled}>
            Use this runtime
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
