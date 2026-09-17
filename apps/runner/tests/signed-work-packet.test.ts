import { describe, expect, it } from "vitest";

import { signWorkPacket, verifySignedWorkPacket, WorkPacketSignatureError } from "../src/contracts/signed-work-packet.js";
import type { WorkPacket } from "../src/contracts/work-packet.js";

function samplePacket(): WorkPacket {
  return {
    schema_version: "1.0.0",
    packet_id: "11111111-1111-1111-1111-111111111111",
    task_type: "IMPLEMENT_STORY",
    project_id: "22222222-2222-2222-2222-222222222222",
    story_id: null,
    objective: { goal: "Fix the bug", success_definition: "Tests pass", non_goals: [] },
    acceptance_criteria: [],
    repository: null,
    upstream_artifacts: [],
    knowledge_context: [],
    scope_policy: { allowed_paths: [], denied_paths: [], deny_by_default: true },
    tool_policy: { allowed_tools: [], denied_tools: [], deny_by_default: true, require_dry_run_first: false },
    required_checks: [],
    budget_policy: {},
    approval_policy: { requires_human_approval: true, approval_roles: [], escalation_roles: [] },
    created_at: "2026-01-01T00:00:00Z",
    created_by_user_id: null,
  };
}

describe("SignedWorkPacket verification", () => {
  it("accepts a packet signed with the correct secret", () => {
    const packet = samplePacket();
    const signature = signWorkPacket(packet, "correct-secret");
    const verified = verifySignedWorkPacket({ packet, signature, execution_profile: {} as never }, "correct-secret");
    expect(verified.packet_id).toBe(packet.packet_id);
  });

  it("rejects a packet signed with the wrong secret", () => {
    const packet = samplePacket();
    const signature = signWorkPacket(packet, "wrong-secret");
    expect(() => verifySignedWorkPacket({ packet, signature, execution_profile: {} as never }, "correct-secret")).toThrow(WorkPacketSignatureError);
  });

  it("rejects a packet whose content was tampered with after signing", () => {
    const packet = samplePacket();
    const signature = signWorkPacket(packet, "correct-secret");
    const tampered = { ...packet, objective: { ...packet.objective, goal: "Do something else entirely" } };
    expect(() => verifySignedWorkPacket({ packet: tampered, signature, execution_profile: {} as never }, "correct-secret")).toThrow(WorkPacketSignatureError);
  });

  it("is insensitive to key ordering (canonicalization)", () => {
    const packet = samplePacket();
    const signature = signWorkPacket(packet, "correct-secret");
    // Build an object literal with a deliberately different key insertion
    // order than samplePacket() — canonicalize() must still produce the
    // same signed bytes for both.
    const reordered: WorkPacket = {
      created_by_user_id: packet.created_by_user_id,
      created_at: packet.created_at,
      approval_policy: packet.approval_policy,
      budget_policy: packet.budget_policy,
      required_checks: packet.required_checks,
      tool_policy: packet.tool_policy,
      scope_policy: packet.scope_policy,
      knowledge_context: packet.knowledge_context,
      upstream_artifacts: packet.upstream_artifacts,
      repository: packet.repository,
      acceptance_criteria: packet.acceptance_criteria,
      objective: packet.objective,
      story_id: packet.story_id,
      project_id: packet.project_id,
      task_type: packet.task_type,
      packet_id: packet.packet_id,
      schema_version: packet.schema_version,
    };
    expect(verifySignedWorkPacket({ packet: reordered, signature, execution_profile: {} as never }, "correct-secret").packet_id).toBe(packet.packet_id);
  });
});
