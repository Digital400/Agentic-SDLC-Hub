import { describe, expect, it } from "vitest";

import { RepoVerificationError, verifyRepoState, type GitRunner } from "../src/repo-verify.js";

function fakeGit(responses: Record<string, string | Error>): GitRunner {
  return async (args) => {
    const key = args.join(" ");
    const match = Object.entries(responses).find(([k]) => key.startsWith(k));
    if (!match) throw new Error(`Unexpected git invocation: ${key}`);
    const [, value] = match;
    if (value instanceof Error) throw value;
    return value;
  };
}

const expected = { remoteUrl: "https://github.com/acme/widgets.git", branch: "main", baseCommitSha: "abc123" };

describe("verifyRepoState", () => {
  it("passes when remote, branch and base commit all match", async () => {
    const git = fakeGit({
      "remote get-url origin": "https://github.com/acme/widgets.git",
      "rev-parse --abbrev-ref HEAD": "main",
      "cat-file -e abc123": "",
    });
    await expect(verifyRepoState(expected, "/repo", git)).resolves.toBeUndefined();
  });

  it("treats an ssh-form remote as equivalent to the https form", async () => {
    const git = fakeGit({
      "remote get-url origin": "git@github.com:acme/widgets.git",
      "rev-parse --abbrev-ref HEAD": "main",
      "cat-file -e abc123": "",
    });
    await expect(verifyRepoState(expected, "/repo", git)).resolves.toBeUndefined();
  });

  it("rejects a mismatched remote", async () => {
    const git = fakeGit({
      "remote get-url origin": "https://github.com/someone-else/widgets.git",
      "rev-parse --abbrev-ref HEAD": "main",
      "cat-file -e abc123": "",
    });
    await expect(verifyRepoState(expected, "/repo", git)).rejects.toThrow(RepoVerificationError);
  });

  it("rejects a mismatched branch", async () => {
    const git = fakeGit({
      "remote get-url origin": "https://github.com/acme/widgets.git",
      "rev-parse --abbrev-ref HEAD": "feature/other",
      "cat-file -e abc123": "",
    });
    await expect(verifyRepoState(expected, "/repo", git)).rejects.toThrow(RepoVerificationError);
  });

  it("rejects a base commit not present locally", async () => {
    const git = fakeGit({
      "remote get-url origin": "https://github.com/acme/widgets.git",
      "rev-parse --abbrev-ref HEAD": "main",
      "cat-file -e abc123": new Error("not found"),
    });
    await expect(verifyRepoState(expected, "/repo", git)).rejects.toThrow(RepoVerificationError);
  });
});
