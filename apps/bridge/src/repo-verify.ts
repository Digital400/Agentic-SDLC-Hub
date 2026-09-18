/**
 * "Verify repository remote, branch and base SHA" — before launching any
 * runtime against the developer's own checkout, confirm it actually is
 * the repository/commit the WorkPacket was written against. This never
 * clones anything (unlike the company-sandbox runner's git-clone.ts) —
 * the developer's existing local checkout is used as-is, only verified.
 */
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export class RepoVerificationError extends Error {}

export interface ExpectedRepoState {
  remoteUrl: string;
  branch: string;
  baseCommitSha: string;
}

export interface GitRunner {
  (args: string[], cwd: string): Promise<string>;
}

export const realGitRunner: GitRunner = async (args, cwd) => {
  const { stdout } = await execFileAsync("git", args, { cwd });
  return stdout.trim();
};

function normalizeRemote(url: string): string {
  // Treat "git@github.com:org/repo.git" and "https://github.com/org/repo.git" as equivalent.
  return url
    .trim()
    .replace(/^git@([^:]+):/, "https://$1/")
    .replace(/\.git$/, "")
    .toLowerCase();
}

export async function verifyRepoState(
  expected: ExpectedRepoState,
  localRepoPath: string,
  git: GitRunner = realGitRunner,
): Promise<void> {
  const remote = await git(["remote", "get-url", "origin"], localRepoPath).catch(() => {
    throw new RepoVerificationError("No 'origin' remote configured in the local repository.");
  });
  if (normalizeRemote(remote) !== normalizeRemote(expected.remoteUrl)) {
    throw new RepoVerificationError(
      `Repository remote mismatch: expected "${expected.remoteUrl}", found "${remote}".`,
    );
  }

  const branch = await git(["rev-parse", "--abbrev-ref", "HEAD"], localRepoPath);
  if (branch !== expected.branch) {
    throw new RepoVerificationError(`Branch mismatch: expected "${expected.branch}", found "${branch}".`);
  }

  // The commit doesn't need to be the exact HEAD, but it must be a real,
  // reachable commit in this local repository — never trust a SHA the
  // server sent without confirming this checkout actually has it.
  await git(["cat-file", "-e", `${expected.baseCommitSha}^{commit}`], localRepoPath).catch(() => {
    throw new RepoVerificationError(`Base commit "${expected.baseCommitSha}" is not present in the local repository.`);
  });
}
