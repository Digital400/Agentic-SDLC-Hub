/**
 * Maps an ACP `session/update` notification's payload onto this runner's
 * normalized RuntimeEventType vocabulary — the ACP sibling of
 * event-mapper.ts's mapOpenCodeEvent, same role and same "never emit
 * chain-of-thought" hard rule (agent_thought_chunk is deliberately never
 * mapped to any event kind — see contracts/acp.ts's HONESTY note on why
 * these payload shapes are best-effort, not verified against a live
 * agent).
 */

import type { AcpSessionUpdate } from "../contracts/acp.js";
import type { NormalizedRuntimeEvent, RuntimeEventType } from "../contracts/events.js";

let sequenceCounter = 0;

export function resetAcpEventSequenceForTest(): void {
  sequenceCounter = 0;
}

const MAX_TEXT_CHARS = 4000; // mirrors event-mapper.ts's own boundText cap

function boundText(text: string | undefined): string | undefined {
  if (!text) return text;
  return text.length > MAX_TEXT_CHARS ? text.slice(0, MAX_TEXT_CHARS) + "…(truncated)" : text;
}

export function mapAcpSessionUpdate(update: AcpSessionUpdate): NormalizedRuntimeEvent | null {
  const base = { sequence: ++sequenceCounter, emittedAt: new Date().toISOString() };

  switch (update.sessionUpdate) {
    case "agent_message_chunk":
      return { ...base, type: "PLAN_SUMMARY" as RuntimeEventType, payload: { text: boundText(update.content.text) } };

    case "agent_thought_chunk":
      return null; // never emit chain-of-thought — see module docstring

    case "tool_call":
      return { ...base, type: "TOOL_REQUEST" as RuntimeEventType, payload: { tool_call_id: update.toolCallId, title: update.title } };

    case "tool_call_update": {
      if (update.status === "completed" || update.status === "failed") {
        const filePaths = (update.content ?? []).filter((c) => c.type === "diff" && c.path).map((c) => c.path);
        return {
          ...base, type: "TOOL_RESULT" as RuntimeEventType,
          payload: { tool_call_id: update.toolCallId, status: update.status, file_paths: filePaths.length > 0 ? filePaths : undefined },
        };
      }
      return { ...base, type: "TOOL_REQUEST" as RuntimeEventType, payload: { tool_call_id: update.toolCallId, status: update.status } };
    }

    case "plan":
      return {
        ...base, type: "PLAN_SUMMARY" as RuntimeEventType,
        payload: { entries: update.entries.map((e) => ({ content: boundText(e.content), status: e.status })) },
      };

    default:
      // An update kind this mapper doesn't recognize (a newer ACP version,
      // or a mismatch with this best-effort implementation — see
      // contracts/acp.ts's HONESTY note) — surfaced as STATUS with the raw
      // kind name, never silently dropped.
      return { ...base, type: "STATUS" as RuntimeEventType, payload: { unrecognized_session_update: (update as { sessionUpdate: string }).sessionUpdate } };
  }
}
