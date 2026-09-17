/**
 * End-to-end integration test for AcpCodingRuntimeAdapter — spawns a REAL
 * child process (tests/fixtures/fake-acp-agent.mjs) speaking real
 * newline-delimited JSON-RPC over stdio, and a REAL local git fixture
 * repository (same cloneUrlOverride pattern git-clone.integration.test.ts
 * already established), so this genuinely exercises the wire protocol and
 * workspace lifecycle end to end — not a mock of AcpCodingRuntimeAdapter
 * itself. See contracts/acp.ts's own HONESTY note: this fixture agent is
 * NOT a real ACP-compliant agent, only something that speaks the same
 * JSON-RPC shape this adapter expects one to.
 */
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AcpCodingRuntimeAdapter } from "../src/runtime/acp-adapter.js";
import { AcpAgentRegistry } from "../src/security/acp-registry.js";
import { signWorkPacket } from "../src/contracts/signed-work-packet.js";
import type { NormalizedRuntimeEvent } from "../src/contracts/events.js";
import type { ProjectExecutionProfile, WorkPacket } from "../src/contracts/work-packet.js";

const FIXTURE_AGENT_PATH = fileURLToPath(new URL("./fixtures/fake-acp-agent.mjs", import.meta.url));
const SECRET = "test-signing-secret";

function git(args: string[], cwd: string): string {
  return execFileSync("git", args, { cwd, encoding: "utf8" }).trim();
}

function samplePacket(overrides: Partial<WorkPacket> = {}, repoBaseSha: string): WorkPacket {
  return {
    schema_version: "1.0.0",
    packet_id: "11111111-1111-1111-1111-111111111111",
    task_type: "IMPLEMENT_STORY",
    project_id: "22222222-2222-2222-2222-222222222222",
    story_id: null,
    objective: { goal: "Add a feature", success_definition: "Tests pass", non_goals: [] },
    acceptance_criteria: [],
    repository: { provider: "github", owner: "acme", name: "widgets", base_branch: "main", base_commit_sha: repoBaseSha },
    upstream_artifacts: [],
    knowledge_context: [],
    scope_policy: { allowed_paths: [], denied_paths: [], deny_by_default: false },
    tool_policy: { allowed_tools: [], denied_tools: [], deny_by_default: false, require_dry_run_first: false },
    required_checks: [],
    budget_policy: {},
    approval_policy: { requires_human_approval: false, approval_roles: [], escalation_roles: [] },
    created_at: "2026-01-01T00:00:00Z",
    created_by_user_id: null,
    ...overrides,
  };
}

function sampleProfile(): ProjectExecutionProfile {
  return {
    id: "p1", project_id: "22222222-2222-2222-2222-222222222222", version: 1, status: "APPROVED",
    working_directories: ["."], approved_security_scan_commands: [], allowed_command_patterns: [],
    denied_command_patterns: [], allowed_paths: ["**"], denied_paths: [],
    network_policy: { default: "DENY" }, environment_variable_names: [], required_coding_skills: [],
    required_testing_skills: [], data_classification: "internal",
  };
}

describe("AcpCodingRuntimeAdapter — end-to-end against a real fake ACP peer process", () => {
  let fixtureRepoDir: string;
  let workspaceRoot: string;
  let baseSha: string;

  beforeEach(async () => {
    fixtureRepoDir = await mkdtemp(path.join(tmpdir(), "acp-fixture-repo-"));
    workspaceRoot = await mkdtemp(path.join(tmpdir(), "acp-workspace-root-"));
    git(["init", "--initial-branch=main"], fixtureRepoDir);
    git(["config", "user.email", "test@example.com"], fixtureRepoDir);
    git(["config", "user.name", "Test"], fixtureRepoDir);
    await writeFile(path.join(fixtureRepoDir, "README.md"), "hello\n");
    git(["add", "."], fixtureRepoDir);
    git(["commit", "-m", "first commit"], fixtureRepoDir);
    baseSha = git(["rev-parse", "HEAD"], fixtureRepoDir);
  });

  afterEach(async () => {
    await rm(fixtureRepoDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    await rm(workspaceRoot, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  });

  function buildAdapter() {
    const registry = new AcpAgentRegistry([
      { key: "fake-agent", executablePath: process.execPath, args: [FIXTURE_AGENT_PATH], protocolVersion: 1, enabled: true },
    ]);
    return new AcpCodingRuntimeAdapter({
      runnerConfig: { workPacketSigningSecret: SECRET, workspaceRoot, maxWallClockSecondsHardCeiling: 30 } as never,
      registry, agentKey: "fake-agent", cloneUrlOverride: fixtureRepoDir,
    });
  }

  it("completes a real end-to-end session and returns the fake agent's file change as evidence", async () => {
    const packet = samplePacket({}, baseSha);
    const signature = signWorkPacket(packet, SECRET);
    const events: NormalizedRuntimeEvent[] = [];

    const result = await buildAdapter().execute({
      signedPacket: { packet, signature, execution_profile: sampleProfile() },
      compiledContext: { systemInstructions: "You are a helpful agent.", responseContractDescription: "Reply with your plan.", metadata: {} },
      onEvent: (e) => events.push(e),
    });

    expect(result.state).toBe("COMPLETED");
    expect(result.file_changes).toHaveLength(1);
    expect(result.file_changes[0]!.path).toBe("README.md");
    expect(events.some((e) => e.type === "STATUS" && (e.payload as { phase?: string }).phase === "acp_initialized")).toBe(true);
    expect(events.some((e) => e.type === "TOOL_RESULT")).toBe(true);
  }, 20000);

  it("refuses to spawn any executable not present in the registry", async () => {
    const packet = samplePacket({}, baseSha);
    const signature = signWorkPacket(packet, SECRET);
    const registry = new AcpAgentRegistry([]); // nothing approved
    const adapter = new AcpCodingRuntimeAdapter({
      runnerConfig: { workPacketSigningSecret: SECRET, workspaceRoot, maxWallClockSecondsHardCeiling: 30 } as never,
      registry, agentKey: "not-approved",
    });

    const result = await adapter.execute({
      signedPacket: { packet, signature, execution_profile: sampleProfile() },
      compiledContext: { systemInstructions: "x", responseContractDescription: "y", metadata: {} },
      onEvent: () => {},
    });

    expect(result.state).toBe("FAILED");
    expect(result.failure?.category).toBe("SECURITY_VIOLATION");
  });

  it("fails closed on a pinned protocol version mismatch, without ever spawning the process", async () => {
    const packet = samplePacket({}, baseSha);
    const signature = signWorkPacket(packet, SECRET);
    const registry = new AcpAgentRegistry([
      { key: "wrong-version", executablePath: process.execPath, args: [FIXTURE_AGENT_PATH], protocolVersion: 99, enabled: true },
    ]);
    const adapter = new AcpCodingRuntimeAdapter({
      runnerConfig: { workPacketSigningSecret: SECRET, workspaceRoot, maxWallClockSecondsHardCeiling: 30 } as never,
      registry, agentKey: "wrong-version",
    });

    const result = await adapter.execute({
      signedPacket: { packet, signature, execution_profile: sampleProfile() },
      compiledContext: { systemInstructions: "x", responseContractDescription: "y", metadata: {} },
      onEvent: () => {},
    });

    expect(result.state).toBe("FAILED");
    expect(result.failure?.category).toBe("SECURITY_VIOLATION");
    expect(result.failure?.message).toContain("protocol version");
  });
});
