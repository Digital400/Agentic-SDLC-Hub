import { mkdtemp, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { createEphemeralWorkspace, WorkspaceDiskLimitExceededError } from "../src/security/workspace.js";

describe("EphemeralWorkspace — steps 2 and 10", () => {
  let root: string;

  afterEach(async () => {
    if (root) await import("node:fs/promises").then((fs) => fs.rm(root, { recursive: true, force: true }));
  });

  it("creates a directory scoped under the given workspace root and disposes it fully", async () => {
    root = await mkdtemp(path.join(tmpdir(), "opencode-runner-root-"));
    const workspace = await createEphemeralWorkspace(root, "job-123");

    expect(workspace.directory.startsWith(root)).toBe(true);
    await expect(stat(workspace.directory)).resolves.toBeDefined();

    await workspace.dispose();
    await expect(stat(workspace.directory)).rejects.toThrow();
  });

  it("gives every workspace a distinct directory even for the same jobId", async () => {
    root = await mkdtemp(path.join(tmpdir(), "opencode-runner-root-"));
    const a = await createEphemeralWorkspace(root, "job-same");
    const b = await createEphemeralWorkspace(root, "job-same");
    expect(a.directory).not.toBe(b.directory);
    await a.dispose();
    await b.dispose();
  });

  it("enforces a disk usage ceiling", async () => {
    root = await mkdtemp(path.join(tmpdir(), "opencode-runner-root-"));
    const workspace = await createEphemeralWorkspace(root, "job-disk");
    await writeFile(path.join(workspace.directory, "big.txt"), "x".repeat(1024));

    await expect(workspace.assertDiskUsageWithin(2048)).resolves.toBeUndefined();
    await expect(workspace.assertDiskUsageWithin(512)).rejects.toThrow(WorkspaceDiskLimitExceededError);

    await workspace.dispose();
  });
});
