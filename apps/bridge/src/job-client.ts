/**
 * HTTP client for the bridge's job lifecycle against the platform's (new,
 * Phase 13) bridge endpoints. Uses the runtime's built-in fetch (Node 20+)
 * so this package has zero HTTP-library dependency.
 */
import { assertNoCredentialFields } from "./credential-guard.js";

export interface AssignedJob {
  job_id: string;
  repository: { remote_url: string; branch: string; base_commit_sha: string };
  runtime_key: string;
  credential_expires_in_seconds: number;
}

export interface SignedWorkPacketEnvelope {
  packet: Record<string, unknown>;
  signature: string;
}

export interface UsageSummary {
  total_tokens: number;
  cost_usd: number;
}

export interface EvidenceUpload {
  job_id: string;
  events: unknown[];
  patch_hash: string | null;
  test_results: unknown[];
  usage: UsageSummary;
}

export class BridgeApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export class BridgeJobClient {
  constructor(private readonly baseUrl: string, private readonly accessToken: string) {}

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: { ...init.headers, Authorization: `Bearer ${this.accessToken}`, "Content-Type": "application/json" },
    });
    if (!res.ok) {
      throw new BridgeApiError(`Bridge API request to ${path} failed with status ${res.status}`, res.status);
    }
    return (await res.json()) as T;
  }

  fetchAssignedJob(): Promise<AssignedJob | null> {
    return this.request<AssignedJob | null>("/bridge/jobs/next");
  }

  acceptJob(jobId: string): Promise<void> {
    return this.request<void>(`/bridge/jobs/${jobId}/accept`, { method: "POST" });
  }

  rejectJob(jobId: string, reason: string): Promise<void> {
    return this.request<void>(`/bridge/jobs/${jobId}/reject`, { method: "POST", body: JSON.stringify({ reason }) });
  }

  fetchSignedWorkPacket(jobId: string): Promise<SignedWorkPacketEnvelope> {
    return this.request<SignedWorkPacketEnvelope>(`/bridge/jobs/${jobId}/work-packet`);
  }

  /** Uploads evidence — refuses to send anything credential-shaped, "never upload the user's runtime credentials." */
  async uploadEvidence(evidence: EvidenceUpload): Promise<void> {
    assertNoCredentialFields(evidence);
    await this.request<void>(`/bridge/jobs/${evidence.job_id}/evidence`, {
      method: "POST",
      body: JSON.stringify(evidence),
    });
  }

  reportStatus(status: "connected" | "offline"): Promise<void> {
    return this.request<void>("/bridge/status", { method: "POST", body: JSON.stringify({ status }) });
  }
}
