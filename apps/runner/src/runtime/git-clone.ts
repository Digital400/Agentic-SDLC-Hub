/**
 * Step 3: "Clone the repository at the immutable base SHA." Uses the
 * system `git` binary directly (no network calls this runner makes
 * itself beyond the clone/fetch) and pins to `base_commit_sha` exactly —
 * never `base_branch`'s current tip — mirroring RepositoryReference's own
 * HARD RULE (apps/api/app/agent_runtime/common.py).
 */

import { spawn } from "node:child_process";

import type { RepositoryReference } from "../contracts/work-packet.js";

export class CloneFailedError extends Error {}

export function buildCloneUrl(repo: RepositoryReference, token: string | undefined): string {
  if (repo.provider !== "github") {
    throw new CloneFailedError(`Unsupported repository provider '${repo.provider}' — only 'github' is wired up in this phase.`);
  }
  const auth = token ? `x-access-token:${token}@` : "";
  return `https://${auth}github.com/${repo.owner}/${repo.name}.git`;
}

async function run(command: string, args: string[], cwd: string, timeoutMs: number): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const child = spawn(command, args, { cwd, stdio: "ignore" });
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new CloneFailedError(`'${command} ${args.join(" ")}' timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    child.on("error", (err) => {
      clearTimeout(timer);
      reject(new CloneFailedError(`'${command} ${args.join(" ")}' failed to start: ${err.message}`));
    });
    child.on("exit", (code) => {
      clearTimeout(timer);
      if (code === 0) resolve();
      else reject(new CloneFailedError(`'${command} ${args.join(" ")}' exited with code ${code}`));
    });
  });
}

/**
 * Clones into `workspaceDir` and hard-resets to `repo.base_commit_sha`,
 * so the checked-out tree is byte-identical to that commit regardless of
 * what `base_branch`'s tip has moved to since. Never a shallow clone of
 * "the branch" alone followed by trusting HEAD — always an explicit
 * checkout of the pinned SHA.
 */
export async function cloneAtBaseSha(
  repo: RepositoryReference,
  workspaceDir: string,
  opts: { token?: string; timeoutMs?: number; cloneUrlOverride?: string } = {},
): Promise<void> {
  const timeoutMs = opts.timeoutMs ?? 120_000;
  // cloneUrlOverride exists only so integration tests can point this
  // function at a local fixture repository (see tests/git-clone.integration.test.ts)
  // without a real GitHub remote — production callers never set it.
  const url = opts.cloneUrlOverride ?? buildCloneUrl(repo, opts.token);
  await run("git", ["clone", "--no-checkout", "--branch", repo.base_branch, "--single-branch", url, "."], workspaceDir, timeoutMs);
  await run("git", ["fetch", "--depth", "1", "origin", repo.base_commit_sha], workspaceDir, timeoutMs);
  await run("git", ["checkout", "--detach", repo.base_commit_sha], workspaceDir, timeoutMs);
}
