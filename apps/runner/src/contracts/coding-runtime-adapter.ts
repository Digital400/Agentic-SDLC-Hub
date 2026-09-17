/**
 * CodingRuntimeAdapter — the contract this phase's instructions name
 * ("Implement OpenCodeRuntimeAdapter through the existing
 * CodingRuntimeAdapter contract") but that did not exist anywhere in this
 * codebase before this phase. Defined here, fresh, following the same
 * strangler-migration pattern every earlier phase in this arc used when its
 * instructions named a not-yet-built contract (Phase 05's ModelGateway,
 * Phase 06's AgentJobDispatcher): the interface comes first, and exactly
 * one concrete implementation (OpenCodeRuntimeAdapter) satisfies it today,
 * leaving room for a second (e.g. a "generic-ci-container" adapter) later
 * without touching any caller of this interface.
 *
 * A CodingRuntimeAdapter's `execute` method is expected to perform, in
 * order, every one of this phase's ten numbered steps:
 *   1. Receive a signed WorkPacket           -> `input.signedPacket` (verified before `execute` is called — see signed-work-packet.ts)
 *   2. Create an isolated ephemeral workspace -> security/workspace.ts
 *   3. Clone the repository at the immutable base SHA -> runtime/git-clone.ts
 *   4. Apply the approved ProjectExecutionProfile -> `input.signedPacket.execution_profile`
 *   5. Compile and load only applicable skills/context -> `input.compiledContext`
 *   6. Route model access through the company model gateway -> security/permissions.ts's provider config
 *   7. Run OpenCode with explicit permissions -> security/permissions.ts's permission config
 *   8. Stream normalized events -> `input.onEvent`, runtime/event-mapper.ts
 *   9. Return patch, changed files, commands, test evidence and usage -> the returned ExecutionResult
 *   10. Dispose of the workspace -> security/workspace.ts's dispose(), always in a `finally`
 */

import type { NormalizedRuntimeEvent } from "./events.js";
import type { ExecutionResult } from "./execution-result.js";
import type { SignedWorkPacket } from "./work-packet.js";

/**
 * Pre-compiled skill/policy/project context (Phase 04's PromptCompiler
 * output) — this runner does not itself compile skills; it receives the
 * already-compiled, already-trimmed instruction text and renders it as
 * OpenCode's `agent.build.prompt` / a leading user-message part. Kept as a
 * bag of already-rendered strings, not raw skill objects, so this runner
 * never needs to know Phase 04's `.sdlc/skills/` catalog shape directly —
 * exactly the same "reference/render, don't re-derive" boundary hard rule 3
 * establishes elsewhere in this arc.
 */
export interface CompiledContext {
  systemInstructions: string;
  responseContractDescription: string;
  /** Non-secret bookkeeping only (compiler hash, skill keys applied) — never a raw credential or chain-of-thought. */
  metadata: Record<string, unknown>;
}

export interface CodingRuntimeExecuteInput {
  signedPacket: SignedWorkPacket;
  compiledContext: CompiledContext;
  /** Called for every normalized event as it happens — the "stream" half of step 8. Must never throw; a throwing sink must not abort execution. */
  onEvent: (event: NormalizedRuntimeEvent) => void;
  /** Cooperative cancellation — checked between steps, not preemptive. Mirrors Phase 06's AgentJobService cancellation model. */
  signal?: AbortSignal;
}

export interface CodingRuntimeAdapter {
  /** A free-text, self-declared label — see app/agent_runtime/capability.py's RuntimeCapabilityManifest.runtime_name, the same vendor-neutral convention. */
  readonly runtimeName: string;

  execute(input: CodingRuntimeExecuteInput): Promise<ExecutionResult>;
}
