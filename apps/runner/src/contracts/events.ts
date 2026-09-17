/**
 * Normalized runtime event vocabulary — a literal mirror of
 * apps/api/app/models/enums.py's AgentJobEventType (Phase 06). Every
 * CodingRuntimeAdapter, OpenCode or otherwise, must only ever emit one of
 * these kinds so a caller polling/streaming a job never needs to know
 * which concrete runtime produced the activity (same "normalized" contract
 * Phase 06's InlineJobDispatcher/CeleryJobDispatcher already honor on the
 * Python side).
 *
 * HARD RULE (carried forward from Phase 06's events.py scrub_chain_of_thought
 * and Phase 00's "never expose chain-of-thought" rule): `payload` must never
 * carry a model's raw reasoning/thinking text. OpenCode's own
 * `reasoning` message part is deliberately NOT mapped to any event kind
 * below — see event-mapper.ts's module docstring.
 */

export const RuntimeEventType = {
  STATUS: "STATUS",
  PLAN_SUMMARY: "PLAN_SUMMARY",
  TOOL_REQUEST: "TOOL_REQUEST",
  TOOL_RESULT: "TOOL_RESULT",
  FILE_CHANGE: "FILE_CHANGE",
  COMMAND: "COMMAND",
  TEST_RESULT: "TEST_RESULT",
  USAGE: "USAGE",
  APPROVAL_REQUIRED: "APPROVAL_REQUIRED",
  ARTIFACT: "ARTIFACT",
  WARNING: "WARNING",
  ERROR: "ERROR",
  COMPLETED: "COMPLETED",
} as const;

export type RuntimeEventType = (typeof RuntimeEventType)[keyof typeof RuntimeEventType];

export interface NormalizedRuntimeEvent {
  type: RuntimeEventType;
  /** Monotonic within one job execution — assigned by the adapter, mirrors AgentJobEvent.sequence. */
  sequence: number;
  emittedAt: string;
  /** Bounded, chain-of-thought-free payload — see module docstring's HARD RULE. */
  payload: Record<string, unknown>;
}
