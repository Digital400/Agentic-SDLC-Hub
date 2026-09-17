/**
 * Maps OpenCode's real Event union (verified against the installed
 * @opencode-ai/sdk@1.18.31 types — see dist/gen/types.gen.d.ts) onto this
 * runner's normalized RuntimeEventType vocabulary (Phase 06's
 * AgentJobEventType mirror). This is step 8, "Stream normalized events."
 *
 * DELIBERATELY UNMAPPED: OpenCode's "message.part.updated" events for a
 * `reasoning` part are never translated into any normalized event — a
 * model's raw reasoning/thinking text must never reach a persisted event
 * payload (mandatory platform rule: never expose chain-of-thought; mirrors
 * Phase 06 events.py's scrub_chain_of_thought). `text` parts ARE mapped
 * (to PLAN_SUMMARY), since final assistant prose is the model's answer,
 * not its hidden reasoning — the same distinction ai_generation.py already
 * draws elsewhere in this codebase.
 */

import type { NormalizedRuntimeEvent, RuntimeEventType } from "../contracts/events.js";

// Minimal structural typing over the real SDK Event union — only the
// fields this mapper actually reads, so this module stays stable even if
// @opencode-ai/sdk adds new event kinds this runner doesn't yet handle
// (an unrecognized `type` is mapped to STATUS with the raw type name
// recorded, never silently dropped).
export interface OpenCodeEventLike {
  type: string;
  properties?: Record<string, unknown>;
}

let sequenceCounter = 0;

export function resetEventSequenceForTest(): void {
  sequenceCounter = 0;
}

export function mapOpenCodeEvent(event: OpenCodeEventLike): NormalizedRuntimeEvent | null {
  const base = { sequence: ++sequenceCounter, emittedAt: new Date().toISOString() };

  switch (event.type) {
    case "session.status":
      return { ...base, type: "STATUS" as RuntimeEventType, payload: { status: event.properties?.status } };

    case "session.idle":
      return { ...base, type: "STATUS" as RuntimeEventType, payload: { status: "idle" } };

    case "message.part.updated": {
      const part = event.properties?.part as
        | { type?: string; text?: string; tool?: string; callID?: string; state?: { status?: string; output?: string; error?: string } }
        | undefined;
      if (part?.type === "text") {
        return { ...base, type: "PLAN_SUMMARY" as RuntimeEventType, payload: { text: boundText(part.text) } };
      }
      if (part?.type === "tool") {
        const status = part.state?.status;
        if (status === "pending" || status === "running") {
          return { ...base, type: "TOOL_REQUEST" as RuntimeEventType, payload: { tool: part.tool, call_id: part.callID } };
        }
        if (status === "completed") {
          return { ...base, type: "TOOL_RESULT" as RuntimeEventType, payload: { tool: part.tool, call_id: part.callID, output: boundText(part.state?.output) } };
        }
        if (status === "error") {
          return { ...base, type: "TOOL_RESULT" as RuntimeEventType, payload: { tool: part.tool, call_id: part.callID, error: boundText(part.state?.error) } };
        }
        return null;
      }
      if (part?.type === "reasoning") {
        return null; // never emit chain-of-thought — see module docstring
      }
      return null;
    }

    case "message.updated": {
      const info = event.properties?.info as { role?: string; cost?: number; tokens?: Record<string, unknown> } | undefined;
      if (info?.role === "assistant" && (info.cost !== undefined || info.tokens !== undefined)) {
        return { ...base, type: "USAGE" as RuntimeEventType, payload: { cost_usd: info.cost, tokens: info.tokens } };
      }
      return null;
    }

    case "file.edited":
      return { ...base, type: "FILE_CHANGE" as RuntimeEventType, payload: { file: event.properties?.file } };

    case "session.diff": {
      const diff = event.properties?.diff as Array<{ file: string; additions: number; deletions: number }> | undefined;
      return { ...base, type: "FILE_CHANGE" as RuntimeEventType, payload: { files: diff?.map((d) => ({ file: d.file, additions: d.additions, deletions: d.deletions })) } };
    }

    case "command.executed":
      return { ...base, type: "COMMAND" as RuntimeEventType, payload: { name: event.properties?.name, arguments: event.properties?.arguments } };

    case "permission.updated":
      return { ...base, type: "APPROVAL_REQUIRED" as RuntimeEventType, payload: { permission: event.properties } };

    case "session.error": {
      const error = event.properties?.error as { message?: string } | undefined;
      return { ...base, type: "ERROR" as RuntimeEventType, payload: { message: boundText(error?.message ?? "unknown OpenCode session error") } };
    }

    default:
      return { ...base, type: "STATUS" as RuntimeEventType, payload: { unmapped_event_type: event.type } };
  }
}

function boundText(text: string | undefined, maxLength = 4000): string | undefined {
  if (text === undefined) return undefined;
  return text.length > maxLength ? text.slice(0, maxLength) + "…" : text;
}
