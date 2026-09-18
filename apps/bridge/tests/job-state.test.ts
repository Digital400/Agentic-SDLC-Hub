import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { JobStateStore, type JobState } from "../src/job-state.js";

describe("JobStateStore", () => {
  let dir: string;

  beforeEach(async () => {
    dir = await mkdtemp(path.join(tmpdir(), "bridge-job-state-"));
  });

  afterEach(async () => {
    await rm(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  });

  function sampleState(overrides: Partial<JobState> = {}): JobState {
    return {
      jobId: "job-1", phase: "accepted", credentialExpiresAt: Date.now() + 60_000,
      workspacePath: "/repo", updatedAt: new Date().toISOString(), ...overrides,
    };
  }

  it("saves and loads a job's state", async () => {
    const store = new JobStateStore(dir);
    await store.save(sampleState());
    const loaded = await store.load("job-1");
    expect(loaded?.phase).toBe("accepted");
  });

  it("returns null for a job that was never saved", async () => {
    const store = new JobStateStore(dir);
    expect(await store.load("nope")).toBeNull();
  });

  it("loadIfCredentialValid returns the state when not yet expired", async () => {
    const store = new JobStateStore(dir);
    await store.save(sampleState({ credentialExpiresAt: Date.now() + 60_000 }));
    const loaded = await store.loadIfCredentialValid("job-1");
    expect(loaded?.jobId).toBe("job-1");
  });

  it("loadIfCredentialValid returns null and deletes the file once the credential has expired", async () => {
    const store = new JobStateStore(dir);
    await store.save(sampleState({ credentialExpiresAt: Date.now() - 1000 }));
    const loaded = await store.loadIfCredentialValid("job-1");
    expect(loaded).toBeNull();
    expect(await store.load("job-1")).toBeNull();
  });

  it("clear removes a saved state without throwing if it never existed", async () => {
    const store = new JobStateStore(dir);
    await expect(store.clear("never-existed")).resolves.toBeUndefined();
  });
});
