import { beforeEach, describe, expect, it } from "vitest";

import { mapOpenCodeEvent, resetEventSequenceForTest } from "../src/runtime/event-mapper.js";

describe("mapOpenCodeEvent — 'Stream normalized events'", () => {
  beforeEach(() => resetEventSequenceForTest());

  it("maps a text message part to PLAN_SUMMARY", () => {
    const mapped = mapOpenCodeEvent({ type: "message.part.updated", properties: { part: { type: "text", text: "Here is my plan." } } });
    expect(mapped?.type).toBe("PLAN_SUMMARY");
    expect(mapped?.payload.text).toBe("Here is my plan.");
  });

  it("never emits a reasoning part — chain-of-thought must never reach a normalized event", () => {
    const mapped = mapOpenCodeEvent({ type: "message.part.updated", properties: { part: { type: "reasoning", text: "secret internal reasoning" } } });
    expect(mapped).toBeNull();
  });

  it("maps a pending tool part to TOOL_REQUEST", () => {
    const mapped = mapOpenCodeEvent({
      type: "message.part.updated",
      properties: { part: { type: "tool", tool: "bash", callID: "call-1", state: { status: "pending" } } },
    });
    expect(mapped?.type).toBe("TOOL_REQUEST");
    expect(mapped?.payload.tool).toBe("bash");
  });

  it("maps a completed tool part to TOOL_RESULT", () => {
    const mapped = mapOpenCodeEvent({
      type: "message.part.updated",
      properties: { part: { type: "tool", tool: "bash", callID: "call-1", state: { status: "completed", output: "ok" } } },
    });
    expect(mapped?.type).toBe("TOOL_RESULT");
    expect(mapped?.payload.output).toBe("ok");
  });

  it("maps file.edited to FILE_CHANGE", () => {
    const mapped = mapOpenCodeEvent({ type: "file.edited", properties: { file: "src/index.ts" } });
    expect(mapped?.type).toBe("FILE_CHANGE");
    expect(mapped?.payload.file).toBe("src/index.ts");
  });

  it("maps command.executed to COMMAND", () => {
    const mapped = mapOpenCodeEvent({ type: "command.executed", properties: { name: "test", arguments: "" } });
    expect(mapped?.type).toBe("COMMAND");
  });

  it("maps session.error to ERROR", () => {
    const mapped = mapOpenCodeEvent({ type: "session.error", properties: { error: { message: "boom" } } });
    expect(mapped?.type).toBe("ERROR");
    expect(mapped?.payload.message).toBe("boom");
  });

  it("maps an unrecognized event type to STATUS rather than silently dropping it", () => {
    const mapped = mapOpenCodeEvent({ type: "some.future.event", properties: {} });
    expect(mapped?.type).toBe("STATUS");
    expect(mapped?.payload.unmapped_event_type).toBe("some.future.event");
  });

  it("assigns strictly increasing sequence numbers across calls", () => {
    const first = mapOpenCodeEvent({ type: "session.idle", properties: {} });
    const second = mapOpenCodeEvent({ type: "session.idle", properties: {} });
    expect(second!.sequence).toBeGreaterThan(first!.sequence);
  });
});
