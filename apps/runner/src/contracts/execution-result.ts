/**
 * TypeScript mirror of apps/api/app/agent_runtime/execution_result.py's
 * ExecutionResult and its evidence sub-contracts (Phase 01) — field names
 * kept identical for direct interop with the Python side.
 *
 * HONESTY RULE (carried forward verbatim from execution_result.py's module
 * docstring): every piece of evidence that could be mistaken for "this was
 * really executed" carries an explicit `real_execution: boolean` field.
 * OpenCodeRuntimeAdapter sets `real_execution: true` on every CommandEvidence
 * and TestEvidence it produces, because — unlike the rest of this codebase
 * (Phase 00 baseline's confirmed no-sandbox finding) — this runner DOES
 * actually spawn a real OpenCode server and run real commands inside a real
 * ephemeral workspace. Never report simulated execution as real (mandatory
 * platform rule); if a command could not actually be run, `real_execution`
 * must be `false`.
 */

export type ExecutionState =
  | "PENDING"
  | "RUNNING"
  | "COMPLETED"
  | "FAILED"
  | "CLARIFICATION_REQUIRED"
  | "APPROVAL_REQUIRED"
  | "CANCELLED"
  | "STALE";

export type ChangeType = "CREATED" | "MODIFIED" | "DELETED" | "RENAMED";
export type TestResult = "PASS" | "FAIL" | "SKIPPED" | "ERROR";
export type FailureCategory =
  | "TRANSIENT_PROVIDER_ERROR"
  | "TIMEOUT"
  | "BUDGET_EXCEEDED"
  | "SCOPE_VIOLATION"
  | "TOOL_POLICY_VIOLATION"
  | "SECURITY_VIOLATION"
  | "INTERNAL_ERROR"
  | "UNKNOWN";

export interface FileChangeEvidence {
  path: string;
  change_type: ChangeType;
  summary: string;
  diff?: string | null;
  content_ref?: string | null;
}

export interface CommandEvidence {
  command: string;
  real_execution: boolean;
  exit_code?: number | null;
  duration_seconds?: number | null;
  stdout_excerpt?: string | null;
  stderr_excerpt?: string | null;
}

export interface TestEvidence {
  name: string;
  result: TestResult;
  real_execution: boolean;
  criterion_id?: string | null;
  notes?: string | null;
  duration_seconds?: number | null;
}

export interface UsageEvidence {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  llm_calls_made: number;
  tool_calls_made: number;
  distinct_tools_used: string[];
  wall_clock_seconds?: number | null;
  repair_attempts_used: number;
}

export interface RuntimeFailure {
  category: FailureCategory;
  message: string;
  retryable: boolean;
}

export interface ClarificationRequest {
  questions: string[];
  blocking: boolean;
  context?: string | null;
}

export interface ExecutionResult {
  schema_version: string;
  result_id: string;
  packet_id: string;
  state: ExecutionState;

  summary?: string | null;
  file_changes: FileChangeEvidence[];
  commands: CommandEvidence[];
  test_evidence: TestEvidence[];
  usage: UsageEvidence;

  failure?: RuntimeFailure | null;
  clarification?: ClarificationRequest | null;

  started_at?: string | null;
  completed_at?: string | null;
}
