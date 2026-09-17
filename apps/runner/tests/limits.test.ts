import { describe, expect, it } from "vitest";

import { BudgetExceededError, BudgetTracker } from "../src/security/limits.js";

describe("BudgetTracker — 'Token, cost and tool-call limits'", () => {
  it("allows usage within every configured ceiling", () => {
    const tracker = new BudgetTracker({ max_cost_usd: 5, max_llm_calls: 3, max_tool_calls: 10 }, 1800);
    tracker.recordLlmCall(1);
    tracker.recordToolCall("bash");
    expect(tracker.snapshot().costUsd).toBe(1);
  });

  it("throws once max_cost_usd is exceeded", () => {
    const tracker = new BudgetTracker({ max_cost_usd: 1 }, 1800);
    expect(() => tracker.recordLlmCall(2)).toThrow(BudgetExceededError);
  });

  it("throws once max_tool_calls is exceeded", () => {
    const tracker = new BudgetTracker({ max_tool_calls: 1 }, 1800);
    tracker.recordToolCall("bash");
    expect(() => tracker.recordToolCall("edit")).toThrow(BudgetExceededError);
  });

  it("throws once max_distinct_tools is exceeded even if max_tool_calls allows more calls", () => {
    const tracker = new BudgetTracker({ max_distinct_tools: 1, max_tool_calls: 10 }, 1800);
    tracker.recordToolCall("bash");
    expect(() => tracker.recordToolCall("edit")).toThrow(BudgetExceededError);
  });

  it("respects the hard wall-clock ceiling even when no BudgetPolicy ceiling is set", async () => {
    const tracker = new BudgetTracker({}, 0); // hard ceiling of 0 seconds — any elapsed time exceeds it
    await new Promise((resolve) => setTimeout(resolve, 5)); // guarantee measurable elapsed time has passed
    expect(() => tracker.assertWithinBudget()).toThrow(BudgetExceededError);
  });
});
