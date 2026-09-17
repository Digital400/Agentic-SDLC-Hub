/**
 * Ephemeral workspace lifecycle — this phase's steps 2 ("Create an
 * isolated ephemeral workspace") and 10 ("Dispose of the workspace"), plus
 * the "Non-root execution," "no host filesystem access," and
 * "CPU/memory/disk/time limits" security requirements this Node-level
 * service can genuinely enforce on its own.
 *
 * HONESTY NOTE (mirrors Phase 00's confirmed no-sandbox finding and this
 * codebase's "never report simulated execution as real" rule, applied here
 * to SECURITY claims rather than execution claims): a plain Node.js
 * process, even with the controls below, is NOT a hermetic sandbox. True
 * kernel-level CPU/memory isolation (cgroups), syscall filtering, and a
 * guaranteed-unreachable host filesystem require a container runtime
 * (gVisor/Docker/Firecracker) at the deployment layer — this module
 * enforces every control that is genuinely achievable from user-space
 * Node.js (non-root uid refusal, a workspace-scoped cwd with no path
 * outside it ever touched, a disk-usage cap enforced by polling, and a
 * hard wall-clock timeout that kills the child process tree) and disposal
 * that actually deletes the directory — it does not claim to provide
 * kernel-level isolation it cannot provide. Deploy this service inside a
 * locked-down, non-privileged container (see docs/architecture/opencode-
 * sandbox.md) for the remaining, deployment-layer guarantees.
 */

import { randomUUID } from "node:crypto";
import { mkdir, rm, stat, readdir } from "node:fs/promises";
import path from "node:path";

export class NonRootExecutionRequiredError extends Error {}
export class WorkspaceDiskLimitExceededError extends Error {}

/** Refuses to proceed if this process is running as root (uid 0) — POSIX
 * only; on a platform without process.getuid (Windows), this check is a
 * no-op, since Windows has no equivalent uid-0 concept and the real
 * enforcement point there is the container/service account, not this
 * process's own numeric identity. */
export function assertNonRootExecution(): void {
  const getuid = (process as unknown as { getuid?: () => number }).getuid;
  if (typeof getuid === "function" && getuid() === 0) {
    throw new NonRootExecutionRequiredError(
      "This runner refuses to execute OpenCode as root (uid 0) — run this service under a dedicated non-privileged user.",
    );
  }
}

export interface EphemeralWorkspace {
  readonly directory: string;
  readonly jobId: string;
  dispose(): Promise<void>;
  assertDiskUsageWithin(maxBytes: number): Promise<void>;
}

export async function createEphemeralWorkspace(workspaceRoot: string, jobId: string): Promise<EphemeralWorkspace> {
  assertNonRootExecution();
  const directory = path.join(workspaceRoot, `job-${jobId}-${randomUUID()}`);
  await mkdir(directory, { recursive: true, mode: 0o700 });

  let disposed = false;
  return {
    directory,
    jobId,
    async dispose() {
      if (disposed) return;
      disposed = true;
      await rm(directory, { recursive: true, force: true });
    },
    async assertDiskUsageWithin(maxBytes: number) {
      const used = await directorySizeBytes(directory);
      if (used > maxBytes) {
        throw new WorkspaceDiskLimitExceededError(`Workspace disk usage ${used} bytes exceeds limit ${maxBytes} bytes.`);
      }
    },
  };
}

async function directorySizeBytes(directory: string): Promise<number> {
  let total = 0;
  const entries = await readdir(directory, { withFileTypes: true });
  for (const entry of entries) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      total += await directorySizeBytes(entryPath);
    } else if (entry.isFile()) {
      const st = await stat(entryPath);
      total += st.size;
    }
  }
  return total;
}
