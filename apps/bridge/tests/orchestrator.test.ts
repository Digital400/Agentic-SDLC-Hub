import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AssignedJob, BridgeJobClient } from "../src/job-client.js";
import { JobStateStore } from "../src/job-state.js";
import { runAssignedJob, type RuntimeLaunchResult } from "../src/orchestrator.js";
import { LocalRuntimeRegistry } from "../src/runtime-registry.js";
import type { GitRunner } from "../src/repo-verify.js";

function sampleJob(overrides: Partial<AssignedJob> = {}): AssignedJob {
  return {
    job_id: "job-1",
    repository: { remote_url: "https://github.com/acme/widgets.git", branch: "main", base_commit_sha: "abc123" },
    runtime_key: "my-agent",
    credential_expires_in_seconds: 3600,
    ...overrides,
  };
}

function fakeClient(overrides: Partial<Record<keyof BridgeJobClient, unknown>> = {}) {
  return {
    acceptJob: vi.fn().mockResolvedValue(undefined),
    rejectJob: vi.fn().mockResolvedValue(undefined),
    fetchSignedWorkPacket: vi.fn().mockResolvedValue({ packet: { objective: "x" }, signature: "sig" }),
    uploadEvidence: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  } as unknown as BridgeJobClient;
}

const goodGit: GitRunner = async (args) => {
  const key = args.join(" ");
  if (key.startsWith("remote get-url origin")) return "https://github.com/acme/widgets.git";
  if (key.startsWith("rev-parse --abbrev-ref HEAD")) return "main";
  if (key.startsWith("cat-file -e")) return "";
  throw new Error(`unexpected: ${key}`);
};

const successResult: RuntimeLaunchResult = {
  events: [{ type: "STATUS" }],
  patchHash: "deadbeef",
  testResults: [],
  usage: { total_tokens: 100, cost_usd: 0.02 },
};

describe("runAssignedJob", () => {
  let dir: string;

  beforeEach(async () => {
    dir = await mkdtemp(path.join(tmpdir(), "bridge-orchestrator-"));
  });

  afterEach(async () => {
    await rm(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  });

  it("completes end to end: accepts, verifies repo, launches runtime, uploads evidence, clears state", async () => {
    const client = fakeClient();
    const registry = new LocalRuntimeRegistry([{ key: "my-agent", executablePath: "/bin/a", args: [], protocol: "acp", enabled: true }]);
    const stateStore = new JobStateStore(dir);
    const launchRuntime = vi.fn().mockResolvedValue(successResult);

    const outcome = await runAssignedJob(sampleJob(), {
      client, stateStore, registry, git: goodGit, launchRuntime, localRepoPath: "/repo",
    });

    expect(outcome).toEqual({ outcome: "completed" });
    expect(client.acceptJob).toHaveBeenCalledWith("job-1");
    expect(client.uploadEvidence).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: "job-1", patch_hash: "deadbeef" }),
    );
    expect(await stateStore.load("job-1")).toBeNull(); // cleared after completion
  });

  it("rejects the job without ever calling the runtime when the runtime key is not approved", async () => {
    const client = fakeClient();
    const registry = new LocalRuntimeRegistry([]); // nothing approved
    const stateStore = new JobStateStore(dir);
    const launchRuntime = vi.fn();

    const outcome = await runAssignedJob(sampleJob(), {
      client, stateStore, registry, git: goodGit, launchRuntime, localRepoPath: "/repo",
    });

    expect(outcome.outcome).toBe("rejected");
    expect(client.acceptJob).not.toHaveBeenCalled();
    expect(launchRuntime).not.toHaveBeenCalled();
    expect(client.rejectJob).toHaveBeenCalledWith("job-1", expect.stringContaining("not an approved"));
  });

  it("rejects the job when local repo verification fails, and clears any saved state", async () => {
    const client = fakeClient();
    const registry = new LocalRuntimeRegistry([{ key: "my-agent", executablePath: "/bin/a", args: [], protocol: "acp", enabled: true }]);
    const stateStore = new JobStateStore(dir);
    const badGit: GitRunner = async (args) => {
      const key = args.join(" ");
      if (key.startsWith("remote get-url origin")) return "https://github.com/wrong/repo.git";
      return "";
    };
    const launchRuntime = vi.fn();

    const outcome = await runAssignedJob(sampleJob(), {
      client, stateStore, registry, git: badGit, launchRuntime, localRepoPath: "/repo",
    });

    expect(outcome.outcome).toBe("rejected");
    expect(launchRuntime).not.toHaveBeenCalled();
    expect(await stateStore.load("job-1")).toBeNull();
  });

  it("refuses to upload evidence once the job credential has expired mid-run", async () => {
    const client = fakeClient();
    const registry = new LocalRuntimeRegistry([{ key: "my-agent", executablePath: "/bin/a", args: [], protocol: "acp", enabled: true }]);
    const stateStore = new JobStateStore(dir);
    let now = 0;
    const launchRuntime = vi.fn().mockImplementation(async () => {
      now = 10_000_000; // simulate a very long-running local job, well past credential expiry
      return successResult;
    });

    const outcome = await runAssignedJob(sampleJob({ credential_expires_in_seconds: 60 }), {
      client, stateStore, registry, git: goodGit, launchRuntime, localRepoPath: "/repo", now: () => now,
    });

    expect(outcome).toEqual({ outcome: "credential_expired" });
    expect(client.uploadEvidence).not.toHaveBeenCalled();
  });
});
