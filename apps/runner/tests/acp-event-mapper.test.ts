import { beforeEach, describe, expect, it } from "vitest";

import { mapAcpSessionUpdate, resetAcpEventSequenceForTest } from "../src/runtime/acp-event-mapper.js";
import type { AcpSessionUpdate } from "../src/contracts/acp.js";

beforeEach(() => {
  resetAcpEventSequenceForTest();
});

describe("mapAcpSessionUpdate", () => {
  it("maps agent_message_chunk to PLAN_SUMMARY", () => {
    const update: AcpSessionUpdate = { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "hello" } };
    const mapped = mapAcpSessionUpdate(update);
    expect(mapped).toEqual({ sequence: 1, emittedAt: expect.any(String), type: "PLAN_SUMMARY", payload: { text: "hello" } });
  });

  it("never emits agent_thought_chunk — no chain-of-thought", () => {
    const update: AcpSessionUpdate = { sessionUpdate: "agent_thought_chunk", content: { type: "text", text: "secret reasoning" } };
    expect(mapAcpSessionUpdate(update)).toBeNull();
  });

  it("maps a fresh tool_call to TOOL_REQUEST", () => {
    const update: AcpSessionUpdate = { sessionUpdate: "tool_call", toolCallId: "t1", title: "Edit file" };
    const mapped = mapAcpSessionUpdate(update);
    expect(mapped!.type).toBe("TOOL_REQUEST");
    expect(mapped!.payload).toEqual({ tool_call_id: "t1", title: "Edit file" });
  });

  it("maps an in-progress tool_call_update to TOOL_REQUEST", () => {
    const update: AcpSessionUpdate = { sessionUpdate: "tool_call_update", toolCallId: "t1", status: "in_progress" };
    expect(mapAcpSessionUpdate(update)!.type).toBe("TOOL_REQUEST");
  });

  it("maps a completed tool_call_update to TOOL_RESULT with file paths from diff content", () => {
    const update: AcpSessionUpdate = {
      sessionUpdate: "tool_call_update", toolCallId: "t1", status: "completed",
      content: [{ type: "diff", path: "a.py", diff: "..." }, { type: "text" }],
    };
    const mapped = mapAcpSessionUpdate(update);
    expect(mapped!.type).toBe("TOOL_RESULT");
    expect(mapped!.payload).toEqual({ tool_call_id: "t1", status: "completed", file_paths: ["a.py"] });
  });

  it("maps a failed tool_call_update to TOOL_RESULT too", () => {
    const update: AcpSessionUpdate = { sessionUpdate: "tool_call_update", toolCallId: "t1", status: "failed" };
    expect(mapAcpSessionUpdate(update)!.type).toBe("TOOL_RESULT");
  });

  it("maps plan to PLAN_SUMMARY with bounded entry content", () => {
    const update: AcpSessionUpdate = { sessionUpdate: "plan", entries: [{ content: "Step 1", status: "pending" }] };
    const mapped = mapAcpSessionUpdate(update);
    expect(mapped!.type).toBe("PLAN_SUMMARY");
    expect(mapped!.payload).toEqual({ entries: [{ content: "Step 1", status: "pending" }] });
  });

  it("maps an unrecognized sessionUpdate kind to STATUS rather than dropping it silently", () => {
    const update = { sessionUpdate: "some_future_kind" } as unknown as AcpSessionUpdate;
    const mapped = mapAcpSessionUpdate(update);
    expect(mapped!.type).toBe("STATUS");
    expect(mapped!.payload).toEqual({ unrecognized_session_update: "some_future_kind" });
  });

  it("assigns a monotonically increasing sequence across calls", () => {
    const a = mapAcpSessionUpdate({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "a" } });
    const b = mapAcpSessionUpdate({ sessionUpdate: "agent_message_chunk", content: { type: "text", text: "b" } });
    expect(b!.sequence).toBe(a!.sequence + 1);
  });
});
