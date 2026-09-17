/**
 * Token, cost, and tool-call budget enforcement — this phase's "Token,
 * cost and tool-call limits" security requirement. Tracked here, in this
 * runner, rather than trusted from OpenCode's own bookkeeping alone,
 * because OpenCode's AssistantMessage.cost/tokens are reported AFTER a
 * call completes — this tracker is what session-event handling calls into
 * as usage streams in, so a budget can be exceeded mid-run and the
 * session aborted, not just reported after the fact.
 */

import type { BudgetPolicy } from "../contracts/work-packet.js";

export class BudgetExceededError extends Error {
  constructor(public readonly reason: string) {
    super(`Budget exceeded: ${reason}`);
  }
}

export class BudgetTracker {
  private costUsd = 0;
  private llmCalls = 0;
  private toolCalls = 0;
  private readonly distinctTools = new Set<string>();
  private readonly startedAtMs = Date.now();

  constructor(private readonly policy: BudgetPolicy, private readonly hardWallClockSecondsCeiling: number) {}

  recordLlmCall(costUsd: number): void {
    this.llmCalls += 1;
    this.costUsd += costUsd;
    this.assertWithinBudget();
  }

  recordToolCall(toolName: string): void {
    this.toolCalls += 1;
    this.distinctTools.add(toolName);
    this.assertWithinBudget();
  }

  snapshot() {
    return {
      costUsd: this.costUsd,
      llmCalls: this.llmCalls,
      toolCalls: this.toolCalls,
      distinctTools: [...this.distinctTools],
      wallClockSeconds: (Date.now() - this.startedAtMs) / 1000,
    };
  }

  assertWithinBudget(): void {
    const s = this.snapshot();
    if (this.policy.max_cost_usd != null && s.costUsd > this.policy.max_cost_usd) {
      throw new BudgetExceededError(`cost_usd ${s.costUsd} > max_cost_usd ${this.policy.max_cost_usd}`);
    }
    if (this.policy.max_llm_calls != null && s.llmCalls > this.policy.max_llm_calls) {
      throw new BudgetExceededError(`llm_calls ${s.llmCalls} > max_llm_calls ${this.policy.max_llm_calls}`);
    }
    if (this.policy.max_tool_calls != null && s.toolCalls > this.policy.max_tool_calls) {
      throw new BudgetExceededError(`tool_calls ${s.toolCalls} > max_tool_calls ${this.policy.max_tool_calls}`);
    }
    if (this.policy.max_distinct_tools != null && s.distinctTools.length > this.policy.max_distinct_tools) {
      throw new BudgetExceededError(`distinct_tools ${s.distinctTools.length} > max_distinct_tools ${this.policy.max_distinct_tools}`);
    }
    const wallClockCeiling = Math.min(
      this.policy.max_wall_clock_seconds ?? this.hardWallClockSecondsCeiling,
      this.hardWallClockSecondsCeiling,
    );
    if (s.wallClockSeconds > wallClockCeiling) {
      throw new BudgetExceededError(`wall_clock_seconds ${s.wallClockSeconds} > ceiling ${wallClockCeiling}`);
    }
  }
}
