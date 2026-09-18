# Incident Response and Sandbox Cleanup

**Audience:** whoever is on call when a coding runtime (company sandbox
or a developer's local bridge) misbehaves, and whoever needs to reason
about what state it can leave behind. Written as the "document incident
response and sandbox cleanup" deliverable Phase 18 asks for.

## 1. Immediate rollback switches

Every system this migration built has its own independent, disabled-by-
default switch — flipping any one of these to `false`/unset requires no
data migration and affects nothing else:

| System | Switch | Where |
|---|---|---|
| ModelGateway | `MODEL_GATEWAY_MODE` (set to `"legacy"`) | Backend `Settings` |
| OpenCode | `OPENCODE_RUNTIME_ENABLED` | Backend `Settings` + `apps/runner`'s own copy |
| ACP | `ACP_RUNTIME_ENABLED` / `ACP_APPROVED_AGENTS_JSON` | `apps/runner`'s own config |
| Story git/PR flow v2 | `STORY_GIT_PR_FLOW_V2_ENABLED` | Backend `Settings` |
| Developer local bridge | `DEVELOPER_BRIDGE_ENABLED` + `apps/bridge`'s own `BRIDGE_ENABLED` | Backend `Settings` + `apps/bridge`'s own config |
| Requirement Intake v2 (shadow) | `REQUIREMENT_INTAKE_AGENT_V2_MODE` (set to `"disabled"`) | Backend `Settings` |
| Automatic cost-aware routing | `RUNTIME_COST_ROUTER_ENABLED` | Backend `Settings` |

`app/services/runtime_rollout.py`'s `current_rollout_status()` reads
every one of these back live — run it (or its future admin-UI surface)
first when triage needs to know "what is actually turned on right now,"
rather than trusting memory or a stale doc.

## 2. What a misbehaving runtime can and cannot do

Read this before assuming the worst. Every adapter this migration built
enforces the same layered constraints (see
`docs/architecture/opencode-sandbox.md` §1.3 and
`docs/architecture/acp-runtime.md` §7 for the authoritative per-adapter
lists):

- **Cannot** push to a remote repository (`git push` is on a fixed
  command denylist, independent of any WorkPacket).
- **Cannot** run a command outside `ProjectExecutionProfile`'s own
  approved patterns (`assertCommandAllowed`, deny-by-default).
- **Cannot** read or write outside its allowed paths
  (`assertPathAllowed`, deny-by-default, blocks `..`/absolute
  paths/`~`/Windows drive letters).
- **Cannot** reach the network by default (`permission.webfetch: "deny"`,
  `NetworkPolicy.default: DENY`).
- **Can** exhaust CPU/memory on its host if not deployed inside a
  container — this is the one limitation every adapter's own doc
  discloses as a **deployment-layer** responsibility, not something the
  adapter code itself hermetically guarantees. If a runtime appears to be
  consuming excessive resources, the fix is at the container/orchestration
  layer, not in adapter code.

## 3. Sandbox cleanup

**Company-sandbox workspaces** (`apps/runner`'s `security/workspace.ts`):
every job gets a fresh, randomly-named directory under
`RUNNER_WORKSPACE_ROOT`, disposed of in a `finally` block after every
run — including on failure or cancellation. If a workspace directory is
found still present after its job should have completed:

1. Confirm the job's process has actually exited (`ps`/Task Manager) —
   do not delete a directory a still-running process has open.
2. If the process is gone but the directory remains, this is the
   Windows `EBUSY` race documented in
   `docs/architecture/runtime-adapter-development.md` §5 — `dispose()`
   already retries this internally (`maxRetries: 5, retryDelay: 200`);
   if it's still present after that, it survived a crash before
   `dispose()` ran at all (e.g. the process was killed externally,
   bypassing the `finally` block). Safe to delete by hand — nothing in
   it is meant to persist past one job.
3. Never delete `RUNNER_WORKSPACE_ROOT` itself while other jobs may be
   running concurrently — only its per-job subdirectories.

**Developer local bridge state** (`apps/bridge`'s `src/job-state.ts`):
lives entirely on the developer's own machine under
`BRIDGE_JOB_STATE_DIR` (default `.agentic-bridge/jobs/`). A stuck job's
state file is safe to delete manually — the server-side
`BridgeJobAssignment` row is the source of truth for whether a job is
actually still owed to that developer; `resumeJobIfPending` already
refuses to resume a job whose credential has expired
(`JobCredentialExpiredError`), and expired local state is deleted
automatically rather than silently reused.

**Server-side bridge evidence**: `BridgeJobAssignment.evidence_trusted`
is permanently `False` for every row this migration's code ever
produces (no CI-verification integration exists yet to flip it — see
`docs/architecture/developer-local-bridge.md` §7). If an incident
involves a bridge job's uploaded evidence, treat it as **exactly as
trusted as an unverified developer claim** — because that is precisely
what this field is honestly telling you.

## 4. Audit trail

Every mutation across the developer bridge, story delivery lanes, and
runtime routing decisions is recorded via `app.services.audit.record_audit_log`
(`AuditLog` table) — `bridge_device.approved`, `bridge_job.assigned`/
`accepted`/`rejected`/`evidence_uploaded`, plus this migration's own
per-phase audit actions. Query `AuditLog` filtered by `project_id`/
`entity_type` first when reconstructing what happened during an
incident — it is the append-only, attributable record every other table
this migration built assumes exists alongside it.

## 5. What is NOT yet covered

- No alerting/paging integration exists for any of these systems —
  detection today is manual (checking `current_rollout_status()`,
  `AuditLog`, or a runtime's own logs), not automated.
- No automated sandbox-leak detection (a periodic sweep for
  orphaned workspace directories) exists — cleanup above is a manual
  runbook, not a scheduled job.
