/**
 * Local, on-disk job state — supports "resume interrupted jobs" and
 * "expire job credentials after completion". State lives only on the
 * developer's machine; nothing here is uploaded.
 */
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";

export type JobPhase = "accepted" | "repo_verified" | "running" | "uploading" | "completed";

export interface JobState {
  jobId: string;
  phase: JobPhase;
  /** Epoch ms after which this job's fetched credential must be treated as expired. */
  credentialExpiresAt: number;
  workspacePath: string;
  updatedAt: string;
}

export class JobCredentialExpiredError extends Error {}

export class JobStateStore {
  constructor(private readonly dir: string) {}

  private filePath(jobId: string): string {
    return path.join(this.dir, `${jobId}.json`);
  }

  async save(state: JobState): Promise<void> {
    await mkdir(this.dir, { recursive: true });
    await writeFile(this.filePath(state.jobId), JSON.stringify(state, null, 2), "utf8");
  }

  async load(jobId: string): Promise<JobState | null> {
    try {
      const raw = await readFile(this.filePath(jobId), "utf8");
      return JSON.parse(raw) as JobState;
    } catch {
      return null;
    }
  }

  /** Loads state only if its credential has not expired; expired state is deleted, never silently reused. */
  async loadIfCredentialValid(jobId: string, now: number = Date.now()): Promise<JobState | null> {
    const state = await this.load(jobId);
    if (!state) return null;
    if (state.credentialExpiresAt <= now) {
      await this.clear(jobId);
      return null;
    }
    return state;
  }

  async clear(jobId: string): Promise<void> {
    await rm(this.filePath(jobId), { force: true });
  }
}
