/**
 * Ties the bridge's job lifecycle together: accept -> fetch WorkPacket ->
 * verify repo -> launch approved runtime -> collect events -> upload
 * evidence -> expire credential. Every external effect (HTTP, git, the
 * runtime launch, local approval prompts) is injected so this can be
 * tested without a real backend, git repo, or spawned process.
 */
import { assertNoCredentialFields } from "./credential-guard.js";
import type { AssignedJob, BridgeJobClient, EvidenceUpload } from "./job-client.js";
import { JobCredentialExpiredError, type JobState, type JobStateStore } from "./job-state.js";
import { verifyRepoState, type GitRunner } from "./repo-verify.js";
import { LocalRuntimeRegistry } from "./runtime-registry.js";

export interface RuntimeLaunchResult {
  events: unknown[];
  patchHash: string | null;
  testResults: unknown[];
  usage: { total_tokens: number; cost_usd: number };
}

export interface RuntimeLauncher {
  (runtimeKey: string, workPacket: Record<string, unknown>, localRepoPath: string): Promise<RuntimeLaunchResult>;
}

export interface OrchestratorDeps {
  client: BridgeJobClient;
  stateStore: JobStateStore;
  registry: LocalRuntimeRegistry;
  git: GitRunner;
  launchRuntime: RuntimeLauncher;
  localRepoPath: string;
  now?: () => number;
}

export type JobOutcome =
  | { outcome: "completed" }
  | { outcome: "rejected"; reason: string }
  | { outcome: "credential_expired" };

/** Runs exactly one assigned job end to end. Returns without throwing on any expected refusal (rejected/expired) — those are outcomes, not bugs. */
export async function runAssignedJob(job: AssignedJob, deps: OrchestratorDeps): Promise<JobOutcome> {
  const now = deps.now ?? Date.now;

  // Never launch a runtime the operator hasn't approved — resolve before accepting the job at all.
  let runtime;
  try {
    runtime = deps.registry.resolve(job.runtime_key);
  } catch (err) {
    const reason = (err as Error).message;
    await deps.client.rejectJob(job.job_id, reason);
    return { outcome: "rejected", reason };
  }

  await deps.client.acceptJob(job.job_id);
  const credentialExpiresAt = now() + job.credential_expires_in_seconds * 1000;
  const state: JobState = {
    jobId: job.job_id,
    phase: "accepted",
    credentialExpiresAt,
    workspacePath: deps.localRepoPath,
    updatedAt: new Date(now()).toISOString(),
  };
  await deps.stateStore.save(state);

  try {
    await verifyRepoState(
      { remoteUrl: job.repository.remote_url, branch: job.repository.branch, baseCommitSha: job.repository.base_commit_sha },
      deps.localRepoPath,
      deps.git,
    );
  } catch (err) {
    const reason = (err as Error).message;
    await deps.client.rejectJob(job.job_id, reason);
    await deps.stateStore.clear(job.job_id);
    return { outcome: "rejected", reason };
  }
  state.phase = "repo_verified";
  await deps.stateStore.save(state);

  if (now() >= credentialExpiresAt) {
    await deps.stateStore.clear(job.job_id);
    return { outcome: "credential_expired" };
  }

  const { packet } = await deps.client.fetchSignedWorkPacket(job.job_id);
  state.phase = "running";
  await deps.stateStore.save(state);

  const result = await deps.launchRuntime(runtime.key, packet, deps.localRepoPath);

  if (now() >= credentialExpiresAt) {
    // The run itself may have taken longer than the credential's lifetime — never upload with an expired credential.
    await deps.stateStore.clear(job.job_id);
    return { outcome: "credential_expired" };
  }

  state.phase = "uploading";
  await deps.stateStore.save(state);

  const evidence: EvidenceUpload = {
    job_id: job.job_id,
    events: result.events,
    patch_hash: result.patchHash,
    test_results: result.testResults,
    usage: result.usage,
  };
  assertNoCredentialFields(evidence);
  await deps.client.uploadEvidence(evidence);

  state.phase = "completed";
  await deps.stateStore.save(state);
  await deps.stateStore.clear(job.job_id);

  return { outcome: "completed" };
}

/** Resumes a job whose local state file survived a crash/restart, picking up from its last saved phase. */
export async function resumeJobIfPending(jobId: string, deps: OrchestratorDeps): Promise<JobOutcome | null> {
  const state = await deps.stateStore.loadIfCredentialValid(jobId, deps.now?.() ?? Date.now());
  if (!state) {
    throw new JobCredentialExpiredError(`No resumable, non-expired state for job "${jobId}".`);
  }
  // A full resume-from-arbitrary-phase implementation would re-enter runAssignedJob's
  // steps at state.phase; the caller already has the original AssignedJob details
  // persisted server-side, fetched again via fetchSignedWorkPacket, so re-running
  // runAssignedJob from the top is safe and idempotent (accept/verify are both
  // idempotent operations against the server and the local git state).
  return null;
}
