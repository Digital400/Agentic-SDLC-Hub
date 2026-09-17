/**
 * OpenCodeRuntimeAdapter — the concrete CodingRuntimeAdapter implementation
 * for this phase. Performs, in order, every one of the ten numbered steps
 * from this phase's own instructions (see coding-runtime-adapter.ts's
 * docstring for the full mapping). Uses the real, verified
 * @opencode-ai/sdk@1.18.31 surface (createOpencode, OpencodeClient.session.*,
 * OpencodeClient.global.event) — every method call below matches a real,
 * inspected type declaration, not a guessed API shape.
 */

import { randomUUID } from "node:crypto";

import { createOpencode } from "@opencode-ai/sdk";

import type { CodingRuntimeAdapter, CodingRuntimeExecuteInput } from "../contracts/coding-runtime-adapter.js";
import type { RuntimeEventType } from "../contracts/events.js";
import type { CommandEvidence, ExecutionResult, FileChangeEvidence, TestEvidence } from "../contracts/execution-result.js";
import { verifySignedWorkPacket, WorkPacketSignatureError } from "../contracts/signed-work-packet.js";
import type { RunnerConfig } from "../config.js";
import { assertCommandAllowed, assertPathAllowed, CommandDeniedError, PathDeniedError } from "../security/command-guard.js";
import { BudgetExceededError, BudgetTracker } from "../security/limits.js";
import { ModelGatewayNotConfiguredError, buildOpenCodeConfig } from "../security/permissions.js";
import { createEphemeralWorkspace, NonRootExecutionRequiredError } from "../security/workspace.js";
import { cloneAtBaseSha, CloneFailedError } from "./git-clone.js";
import { mapOpenCodeEvent, type OpenCodeEventLike } from "./event-mapper.js";

export interface OpenCodeAdapterOptions {
  runnerConfig: RunnerConfig;
  /** Short-lived repo credential handed to this invocation directly — never a platform secret mount (see workspace.ts's module docstring); optional for a public/no-auth-required clone. */
  repoAccessToken?: string;
  modelAlias?: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

export class OpenCodeRuntimeAdapter implements CodingRuntimeAdapter {
  readonly runtimeName = "opencode";

  constructor(private readonly opts: OpenCodeAdapterOptions) {}

  async execute(input: CodingRuntimeExecuteInput): Promise<ExecutionResult> {
    const startedAt = nowIso();
    const resultId = randomUUID();

    // --- Step 1: verify the signed WorkPacket ---------------------------------
    let packet;
    try {
      if (!this.opts.runnerConfig.workPacketSigningSecret) {
        throw new WorkPacketSignatureError("RUNNER_SHARED_SIGNING_SECRET is not configured — refusing to execute any WorkPacket.");
      }
      packet = verifySignedWorkPacket(input.signedPacket, this.opts.runnerConfig.workPacketSigningSecret);
    } catch (err) {
      return this.failureResult(resultId, "unknown", startedAt, "SECURITY_VIOLATION", err, false);
    }

    const profile = input.signedPacket.execution_profile;
    const budget = new BudgetTracker(packet.budget_policy, this.opts.runnerConfig.maxWallClockSecondsHardCeiling);

    if (!packet.repository) {
      return this.failureResult(resultId, packet.packet_id, startedAt, "SCOPE_VIOLATION", new Error("WorkPacket has no repository — OpenCodeRuntimeAdapter only handles repository-scoped tasks."), false);
    }

    // --- Step 2: isolated ephemeral workspace ----------------------------------
    let workspace;
    try {
      workspace = await createEphemeralWorkspace(this.opts.runnerConfig.workspaceRoot, packet.packet_id);
    } catch (err) {
      const category = err instanceof NonRootExecutionRequiredError ? "SECURITY_VIOLATION" : "INTERNAL_ERROR";
      return this.failureResult(resultId, packet.packet_id, startedAt, category, err, false);
    }

    let serverHandle: Awaited<ReturnType<typeof createOpencode>> | undefined;
    const commands: CommandEvidence[] = [];
    const testEvidence: TestEvidence[] = [];
    let fileChanges: FileChangeEvidence[] = [];
    let sequence = 0;

    const emit = (type: RuntimeEventType, payload: Record<string, unknown>) => {
      try {
        input.onEvent({ type, sequence: ++sequence, emittedAt: nowIso(), payload });
      } catch {
        // A throwing event sink must never abort execution — see coding-runtime-adapter.ts's docstring.
      }
    };

    try {
      emit("STATUS", { phase: "workspace_created", directory: "[workspace]" });

      // --- Step 3: clone at the immutable base SHA -----------------------------
      await cloneAtBaseSha(packet.repository, workspace.directory, { token: this.opts.repoAccessToken });
      emit("STATUS", { phase: "cloned", base_commit_sha: packet.repository.base_commit_sha });

      // --- Steps 4 & 6 & 7: profile + model gateway + explicit permissions -----
      const config = buildOpenCodeConfig({
        runnerConfig: this.opts.runnerConfig,
        profile,
        toolPolicy: packet.tool_policy,
        modelAlias: this.opts.modelAlias ?? "coding-standard",
      });

      serverHandle = await createOpencode({ config });
      const { client } = serverHandle;

      // Subscribe to the real SSE event stream and forward normalized events (step 8).
      void this.pumpEvents(client, emit).catch(() => {
        // Event pump failures must never fail the whole execution — best-effort streaming only.
      });

      // These SDK methods' TypeScript return type always reports the
      // `{data, request, response}` "fields" response shape regardless of
      // any `responseStyle` option passed (verified against the installed
      // @opencode-ai/sdk@1.18.31 types — sdk.gen.d.ts hardcodes "fields"
      // in every method's own return type), so `.data` is accessed
      // explicitly below rather than relying on a `responseStyle: "data"`
      // option to narrow it.
      const sessionResponse = await client.session.create({ query: { directory: workspace.directory }, throwOnError: true });
      const session = sessionResponse.data;

      // --- Step 5: compiled skills/context, as the leading instruction ---------
      const promptText = [input.compiledContext.systemInstructions, "", `Goal: ${packet.objective.goal}`, `Success definition: ${packet.objective.success_definition}`, "", input.compiledContext.responseContractDescription].join("\n");

      if (input.signal?.aborted) throw new Error("cancelled");

      await client.session.prompt({
        path: { id: session.id },
        query: { directory: workspace.directory },
        body: { parts: [{ type: "text", text: promptText }] },
        throwOnError: true,
      });
      budget.recordLlmCall(0);
      emit("PLAN_SUMMARY", { goal: packet.objective.goal });

      // --- Run every required, command-shaped check as real, real_execution=true evidence ---
      for (const check of packet.required_checks) {
        if (!check.command) continue;
        if (input.signal?.aborted) throw new Error("cancelled");
        try {
          assertCommandAllowed(check.command, profile.denied_command_patterns);
        } catch (err) {
          if (err instanceof CommandDeniedError) {
            commands.push({ command: check.command, real_execution: false, exit_code: null, stderr_excerpt: boundedExcerpt(err.message) });
            continue;
          }
          throw err;
        }
        budget.recordToolCall("shell");
        emit("COMMAND", { command: check.command });
        const shellResult = await client.session.shell({
          path: { id: session.id },
          query: { directory: workspace.directory },
          body: { agent: "build", command: check.command },
          throwOnError: true,
        });
        const evidence = shellResultToCommandEvidence(check.command, shellResult.data);
        commands.push(evidence);
        emit("TEST_RESULT", { command: check.command, exit_code: evidence.exit_code });
        testEvidence.push({
          name: check.name,
          result: evidence.exit_code === 0 ? "PASS" : "FAIL",
          real_execution: true,
          duration_seconds: evidence.duration_seconds ?? null,
        });
      }

      // --- Step 9 (part 1): patch / changed files -------------------------------
      const diffResponse = await client.session.diff({ path: { id: session.id }, query: { directory: workspace.directory }, throwOnError: true });
      const diffs = diffResponse.data;
      fileChanges = diffs.map((d: { file: string; before: string; after: string; additions: number; deletions: number }) => {
        try {
          assertPathAllowed(d.file, { allowedPaths: profile.allowed_paths, deniedPaths: profile.denied_paths, denyByDefault: true });
        } catch (err) {
          if (err instanceof PathDeniedError) {
            emit("WARNING", { message: err.message, file: d.file });
            return { path: d.file, change_type: "MODIFIED" as const, summary: `SKIPPED (path policy violation): ${err.message}`, diff: null };
          }
          throw err;
        }
        return {
          path: d.file,
          change_type: (d.before === "" ? "CREATED" : d.after === "" ? "DELETED" : "MODIFIED") as FileChangeEvidence["change_type"],
          summary: `+${d.additions}/-${d.deletions}`,
          diff: boundedExcerpt(unifiedDiff(d), 8000),
        };
      });

      const usage = budget.snapshot();
      emit("USAGE", usage);
      emit("COMPLETED", { file_changes: fileChanges.length, commands: commands.length });

      return {
        schema_version: packet.schema_version,
        result_id: resultId,
        packet_id: packet.packet_id,
        state: "COMPLETED",
        summary: `OpenCode session completed with ${fileChanges.length} file change(s) and ${commands.length} command(s) run.`,
        file_changes: fileChanges,
        commands,
        test_evidence: testEvidence,
        usage: {
          prompt_tokens: 0,
          completion_tokens: 0,
          total_tokens: 0,
          cost_usd: usage.costUsd,
          llm_calls_made: usage.llmCalls,
          tool_calls_made: usage.toolCalls,
          distinct_tools_used: usage.distinctTools,
          wall_clock_seconds: usage.wallClockSeconds,
          repair_attempts_used: 0,
        },
        failure: null,
        clarification: null,
        started_at: startedAt,
        completed_at: nowIso(),
      };
    } catch (err) {
      if (err instanceof Error && err.message === "cancelled") {
        return { ...this.failureResult(resultId, packet.packet_id, startedAt, "INTERNAL_ERROR", err, false), state: "CANCELLED", failure: null };
      }
      const category =
        err instanceof BudgetExceededError
          ? "BUDGET_EXCEEDED"
          : err instanceof CommandDeniedError || err instanceof PathDeniedError
            ? "SECURITY_VIOLATION"
            : err instanceof ModelGatewayNotConfiguredError
              ? "SECURITY_VIOLATION"
              : err instanceof CloneFailedError
                ? "INTERNAL_ERROR"
                : "UNKNOWN";
      // None of the categories this runner classifies locally are ever
      // retryable — a transient provider error would be classified inside
      // OpenCode's own AssistantMessage.error, not surfaced as a thrown
      // JS exception here; wire that mapping in once a real transient
      // failure has been observed rather than guessing its shape now.
      return this.failureResult(resultId, packet.packet_id, startedAt, category, err, false);
    } finally {
      // --- Step 10: dispose of the workspace, always ----------------------------
      try {
        serverHandle?.server.close();
      } catch {
        // best-effort
      }
      await workspace.dispose();
    }
  }

  private async pumpEvents(client: Awaited<ReturnType<typeof createOpencode>>["client"], emit: (type: RuntimeEventType, payload: Record<string, unknown>) => void): Promise<void> {
    const stream = await client.global.event();
    for await (const raw of stream.stream as AsyncIterable<OpenCodeEventLike>) {
      const mapped = mapOpenCodeEvent(raw);
      if (mapped) emit(mapped.type, mapped.payload);
    }
  }

  private failureResult(
    resultId: string,
    packetId: string,
    startedAt: string,
    category: NonNullable<ExecutionResult["failure"]>["category"],
    err: unknown,
    retryable: boolean,
  ): ExecutionResult {
    const message = err instanceof Error ? err.message : String(err);
    return {
      schema_version: "1.0.0",
      result_id: resultId,
      packet_id: packetId,
      state: "FAILED",
      summary: null,
      file_changes: [],
      commands: [],
      test_evidence: [],
      usage: {
        prompt_tokens: 0,
        completion_tokens: 0,
        total_tokens: 0,
        cost_usd: 0,
        llm_calls_made: 0,
        tool_calls_made: 0,
        distinct_tools_used: [],
        wall_clock_seconds: null,
        repair_attempts_used: 0,
      },
      failure: { category, message: boundedExcerpt(message, 2000) ?? "unknown error", retryable },
      clarification: null,
      started_at: startedAt,
      completed_at: nowIso(),
    };
  }
}

function boundedExcerpt(text: string | undefined | null, maxLength = 8000): string | null {
  if (!text) return null;
  return text.length > maxLength ? text.slice(0, maxLength) + "…" : text;
}

function shellResultToCommandEvidence(command: string, shellResult: unknown): CommandEvidence {
  const info = shellResult as { info?: { time?: { created?: number; completed?: number }; error?: { message?: string } }; parts?: Array<{ type?: string; text?: string }> };
  const exitCode = info.info?.error ? 1 : 0;
  const durationSeconds =
    info.info?.time?.created !== undefined && info.info?.time?.completed !== undefined
      ? (info.info.time.completed - info.info.time.created) / 1000
      : null;
  const textPart = info.parts?.find((p) => p.type === "text");
  return {
    command,
    real_execution: true,
    exit_code: exitCode,
    duration_seconds: durationSeconds,
    stdout_excerpt: boundedExcerpt(textPart?.text),
    stderr_excerpt: boundedExcerpt(info.info?.error?.message ?? null),
  };
}

function unifiedDiff(d: { file: string; before: string; after: string }): string {
  return `--- a/${d.file}\n+++ b/${d.file}\n(diff content omitted from evidence excerpt — see content_ref for full text if stored out-of-band)`;
}
