/**
 * Fixture-repository integration test for step 3, "Clone the repository at
 * the immutable base SHA." Builds a REAL local git repository with two
 * commits, then verifies cloneAtBaseSha checks out the tree exactly as it
 * was at the FIRST commit even though the fixture repo's branch tip has
 * since moved to the second — proving base_branch's current tip is never
 * trusted, only base_commit_sha (RepositoryReference's own HARD RULE).
 */
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { cloneAtBaseSha } from "../src/runtime/git-clone.js";

function git(args: string[], cwd: string): string {
  return execFileSync("git", args, { cwd, encoding: "utf8" }).trim();
}

describe("cloneAtBaseSha — fixture repository integration", () => {
  let fixtureRepoDir: string;
  let workspaceDir: string;
  let firstCommitSha: string;

  beforeEach(async () => {
    fixtureRepoDir = await mkdtemp(path.join(tmpdir(), "opencode-runner-fixture-"));
    workspaceDir = await mkdtemp(path.join(tmpdir(), "opencode-runner-workspace-"));

    git(["init", "--initial-branch=main"], fixtureRepoDir);
    git(["config", "user.email", "test@example.com"], fixtureRepoDir);
    git(["config", "user.name", "Test"], fixtureRepoDir);

    await writeFile(path.join(fixtureRepoDir, "README.md"), "version 1\n");
    git(["add", "."], fixtureRepoDir);
    git(["commit", "-m", "first commit"], fixtureRepoDir);
    firstCommitSha = git(["rev-parse", "HEAD"], fixtureRepoDir);

    // A second commit moves the branch tip forward — cloneAtBaseSha must
    // still resolve to the FIRST commit's content, since that's the
    // immutable base_commit_sha this WorkPacket was pinned to.
    await writeFile(path.join(fixtureRepoDir, "README.md"), "version 2 — should never be checked out\n");
    git(["add", "."], fixtureRepoDir);
    git(["commit", "-m", "second commit"], fixtureRepoDir);
  });

  afterEach(async () => {
    await rm(fixtureRepoDir, { recursive: true, force: true });
    await rm(workspaceDir, { recursive: true, force: true });
  });

  it("checks out the tree at base_commit_sha, ignoring later commits on the same branch", async () => {
    await cloneAtBaseSha(
      { provider: "github", owner: "acme", name: "widgets", base_branch: "main", base_commit_sha: firstCommitSha },
      workspaceDir,
      { cloneUrlOverride: fixtureRepoDir },
    );

    const checkedOutSha = git(["rev-parse", "HEAD"], workspaceDir);
    expect(checkedOutSha).toBe(firstCommitSha);

    const content = await readFile(path.join(workspaceDir, "README.md"), "utf8");
    expect(content).toBe("version 1\n");
  });
});
