#!/usr/bin/env node
/**
 * Real CLI entrypoint — wires the tested modules in orchestrator.ts,
 * device-auth.ts, job-client.ts etc. to real I/O (fetch, stdin, git,
 * child_process). Intentionally thin: everything with a decision to make
 * is unit-tested elsewhere with injected fakes; this file only wires them.
 */
import { loadBridgeConfig } from "./config.js";
import { runDeviceAuthorization, type DeviceAuthHttpClient, type DeviceAuthorizationResponse, type DeviceTokenResponse } from "./device-auth.js";
import { BridgeJobClient } from "./job-client.js";
import { JobStateStore } from "./job-state.js";
import { realGitRunner } from "./repo-verify.js";
import { parseApprovedLocalRuntimes, LocalRuntimeRegistry } from "./runtime-registry.js";
import { runAssignedJob } from "./orchestrator.js";

async function main(): Promise<void> {
  const config = loadBridgeConfig();
  if (!config.enabled) {
    console.error("Developer bridge is disabled. Set BRIDGE_ENABLED=true to run it.");
    process.exitCode = 1;
    return;
  }

  const deviceHttpClient: DeviceAuthHttpClient = {
    async requestDeviceCode(clientId) {
      const res = await fetch(`${config.apiBaseUrl}/bridge/device/authorize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: clientId }),
      });
      return (await res.json()) as DeviceAuthorizationResponse;
    },
    async pollToken(clientId, deviceCode) {
      const res = await fetch(`${config.apiBaseUrl}/bridge/device/token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: clientId, device_code: deviceCode }),
      });
      return (await res.json()) as { status: "pending" | "denied" | "approved"; token?: DeviceTokenResponse };
    },
  };

  const token = await runDeviceAuthorization(deviceHttpClient, config.deviceClientId, (auth) => {
    console.log(`Visit ${auth.verification_uri} and enter code: ${auth.user_code}`);
  });

  const client = new BridgeJobClient(config.apiBaseUrl, token.access_token);
  const stateStore = new JobStateStore(config.jobStateDir);
  const registry = new LocalRuntimeRegistry(parseApprovedLocalRuntimes(config.approvedRuntimesJson));

  await client.reportStatus("connected");
  const job = await client.fetchAssignedJob();
  if (!job) {
    console.log("No job assigned.");
    return;
  }

  const outcome = await runAssignedJob(job, {
    client,
    stateStore,
    registry,
    git: realGitRunner,
    localRepoPath: config.workspaceRoot,
    // A real runtime launcher (ACP/native process spawn + local approval prompting)
    // is a follow-up wiring step once a real approved local runtime binary is
    // available to validate against — see docs/architecture/developer-local-bridge.md
    // section on remaining risks. Throwing here rather than fabricating a fake
    // success keeps this CLI honest about what is and isn't wired yet.
    launchRuntime: () => {
      throw new Error("No RuntimeLauncher wired yet — see docs/architecture/developer-local-bridge.md.");
    },
  });

  console.log(`Job ${job.job_id}: ${outcome.outcome}`);
  await client.reportStatus("offline");
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
