# Generic ACP Coding Runtime (Phase 11)

**Status:** New, additive, standalone `apps/runner` module, registered as
available-but-disabled coding runtimes (one entry per admin-approved agent).
Nothing in the existing FastAPI backend, database schema, or Phase 08
OpenCode runtime changed.

**Honesty note:** this adapter is written against the publicly documented
[Agent Client Protocol](https://agentclientprotocol.com) message shapes
(`contracts/acp.ts`). It has been verified end-to-end against a real child
process speaking real newline-delimited JSON-RPC 2.0 over stdio
(`tests/fixtures/fake-acp-agent.mjs`, exercised by
`tests/acp-adapter.integration.test.ts`), but that fixture is **not** a real
ACP-compliant agent binary — this environment has no network access and no
real ACP agent to test against. Treat this as best-effort, protocol-shape-
verified, not vendor-certified.

## 1. How a new ACP-compatible runtime is registered

There is no per-runtime code to write. Every ACP agent is a **data entry**,
never a code change, in one operator-controlled environment variable:
`ACP_APPROVED_AGENTS_JSON` (`apps/runner/src/security/acp-registry.ts`).

```json
[
  {
    "key": "my-coding-agent",
    "executablePath": "/usr/local/bin/my-coding-agent",
    "args": ["--acp"],
    "protocolVersion": 1,
    "enabled": true
  }
]
```

- **`key`** — the short name a `WorkPacket`/caller may request. This is the
  *only* thing ever visible outside this runner; a WorkPacket can never name
  an executable path directly.
- **`executablePath`** / **`args`** — the real binary and its invocation
  arguments. Set by whoever controls the deployment environment, never by
  request-time input.
- **`protocolVersion`** — the ACP protocol version this agent was validated
  against. The adapter pins this per agent (see §2) and refuses to proceed
  if the agent's live `initialize` response disagrees.
- **`enabled`** — per-agent kill switch, independent of the master flag
  below.

`parseApprovedAcpAgents()` parses this JSON once at process startup
(`apps/runner/src/registry.ts`'s `buildRuntimeRegistry()`). Malformed JSON,
a non-array value, or an entry missing a required field is **skipped, never
thrown** — the registry fails closed to "no agents approved," not to a
crash or to "allow anything."

Each approved agent becomes one runtime registry entry named `acp:<key>`
(e.g. `acp:my-coding-agent`), listed alongside `opencode` — an operator or
future admin UI can see every configured ACP agent and its enabled state
without needing to read environment variables directly.

## 2. How it is validated

Validation happens in two places, both fail-closed:

1. **At registry build time** — `parseApprovedAcpAgents()` drops any entry
   missing `key` / `executablePath` / `protocolVersion`, or with the wrong
   field types. A partially-malformed list keeps its valid entries; it
   never discards the whole list over one bad entry, and it never treats a
   parse failure as "everything is allowed."
2. **At execution time** — `AcpCodingRuntimeAdapter.execute()`
   (`apps/runner/src/runtime/acp-adapter.ts`):
   - Resolves the requested agent **key** through
     `AcpAgentRegistry.resolve()`. An unknown key and a known-but-*disabled*
     key raise the exact same `AcpAgentNotApprovedError` message — a caller
     can never distinguish "never heard of this agent" from "this agent
     exists but is turned off," which would otherwise leak registry
     contents to an unauthorized caller.
   - Spawns the process, sends `initialize`, and compares the agent's
     reported `protocolVersion` against the registry's pinned value
     **before** ever sending a `session/new` or `session/prompt` call. A
     mismatch fails the run closed (`FAILED` / `SECURITY_VIOLATION`)
     without the agent ever seeing a real work packet — "pin adapter/
     runtime versions," made structural rather than advisory.
   - The executable path and args used to `spawn()` always come from the
     registry entry resolved by key — never from any field on the
     `WorkPacket`, `SignedWorkPacket`, or `ProjectExecutionProfile`. This is
     the same "do not allow users to provide arbitrary executable paths"
     rule Phase 09's check executor and Phase 08's OpenCode adapter already
     enforce for their own command surfaces.

## 3. How it is enabled by an administrator

Two independent switches, matching Phase 08's own two-flag precedent
(`docs/architecture/opencode-sandbox.md` §2) — a runtime is only actually
runnable when *both* agree:

- **`ACP_RUNTIME_ENABLED`** (`apps/runner/src/config.ts`, default `false`)
  — the master switch for the whole ACP subsystem. `false` means no
  `acp:*` runtime is ever handed out by `getEnabledAdapter()`, regardless
  of what `ACP_APPROVED_AGENTS_JSON` contains.
- **Each agent's own `"enabled"` field** in `ACP_APPROVED_AGENTS_JSON` —
  a per-agent kill switch, so an operator can disable one misbehaving agent
  without turning off ACP entirely.

An `acp:<key>` runtime is enabled only when
`ACP_RUNTIME_ENABLED === true AND agent.enabled === true`
(`apps/runner/src/registry.ts`). Requesting a disabled runtime raises a
descriptive error naming both flags an administrator needs to check.

**Rollback:** set `ACP_RUNTIME_ENABLED=false` (the default) or remove/flip
`enabled: false` on the specific agent entry — no data migration, no
schema change; every ACP file added by this phase is additive and imported
by nothing outside `apps/runner`'s own registry.

## 4. The ten steps, and where each lives

Same ten-step contract as Phase 08's `OpenCodeRuntimeAdapter`
(`docs/architecture/opencode-sandbox.md` §1.2) — `AcpCodingRuntimeAdapter`
implements the identical `CodingRuntimeAdapter` interface:

| # | Step | Implementation |
|---|------|-----------------|
| 1 | Receive a signed WorkPacket | `contracts/signed-work-packet.ts` (shared, unchanged from Phase 08) |
| 2 | Isolated ephemeral workspace | `security/workspace.ts` (shared, unchanged) |
| 3 | Clone at immutable base SHA | `runtime/git-clone.ts` (shared, unchanged) |
| 4 | Apply approved ProjectExecutionProfile | `assertPathAllowed`/command policy, reused from Phase 08 |
| 5 | Compile and load only applicable skills/context | `CompiledContext` sent as the ACP session's leading prompt |
| 6/7 | Route model access / run with explicit permissions | Delegated to the agent process itself — an ACP agent manages its own model access; this runner only controls *which* agent may run and on what workspace |
| 8 | Stream normalized events | `runtime/acp-event-mapper.ts`'s `mapAcpSessionUpdate()` maps ACP's `session/update` notifications onto the same `NormalizedRuntimeEvent` vocabulary Phase 06/08 already use — `agent_thought_chunk` is never mapped to any event (no chain-of-thought exposure) |
| 9 | Return patch, changed files, commands, test evidence, usage | `ExecutionResult`, built from the agent's `session/prompt` response and any `tool_call_update` diffs observed |
| 10 | Dispose of the workspace | `workspace.dispose()` in a `finally` block, always runs |

The wire transport itself — `runtime/acp-json-rpc.ts`'s `JsonRpcConnection`
— is newline-delimited JSON-RPC 2.0 over the child process's stdio, with
per-request timeouts (`JsonRpcTimeoutError`), peer-error surfacing
(`JsonRpcPeerError`), and clean shutdown handling (`JsonRpcClosedError`).

## 5. Files changed

- **New:** `apps/runner/src/contracts/acp.ts`
- **New:** `apps/runner/src/runtime/acp-json-rpc.ts`
- **New:** `apps/runner/src/runtime/acp-event-mapper.ts`
- **New:** `apps/runner/src/runtime/acp-adapter.ts`
- **New:** `apps/runner/src/security/acp-registry.ts`
- **New:** `apps/runner/tests/acp-json-rpc.test.ts`,
  `acp-event-mapper.test.ts`, `acp-registry.test.ts`,
  `acp-adapter.integration.test.ts`, `tests/fixtures/fake-acp-agent.mjs`
- **New:** `docs/architecture/acp-runtime.md` (this file)
- **Modified:** `apps/runner/src/config.ts` — added `acpRuntimeEnabled`,
  `acpApprovedAgentsJson`
- **Modified:** `apps/runner/src/registry.ts` — registers one `acp:<key>`
  entry per approved agent
- **Modified:** `apps/runner/src/security/workspace.ts` — `dispose()` now
  retries its directory removal (`maxRetries: 5, retryDelay: 200`) to
  absorb a Windows-only race where a just-killed child process briefly
  still holds its cwd; a real bug this phase's own integration test
  surfaced, not present in any prior phase's test coverage because no
  earlier adapter spawned a raw child process the way this one does.

No Alembic migration — no backend database schema changed.

## 6. Tests executed and results

```
apps/runner> yarn workspace @agentic-sdlc-hub/runner typecheck   → 0 errors
apps/runner> yarn workspace @agentic-sdlc-hub/runner test        → 14 test files, 100 tests, all passing
```

New this phase: `acp-json-rpc.test.ts` (12), `acp-event-mapper.test.ts`
(9), `acp-registry.test.ts` (10), `acp-adapter.integration.test.ts` (3,
spawning the real fixture agent process over real stdio).

Two real bugs were found and fixed via this test suite before completion:
- The integration test initially hung its full timeout because
  `AcpAdapterOptions` had no way to override the git clone URL, so it tried
  to clone a fake `https://github.com/...` URL with no network access.
  Fixed by adding `cloneUrlOverride` and threading it through
  `cloneAtBaseSha()`.
- `result.file_changes` came back empty even after the process completed
  successfully — the test's own `ProjectExecutionProfile.allowed_paths: []`
  triggered the adapter's `denyByDefault: true` path allowlist, matching
  Phase 08's own conservative security default exactly (not an adapter
  bug). Fixed by widening the *test's* fixture profile to `["**"]`.
- The Windows `dispose()` EBUSY race described in §5, caught only when
  running the full suite together (not in isolation), fixed in
  `workspace.ts` itself since any future adapter spawning a raw child
  process would hit the same race.

## 7. Security considerations

- **Admin-approved-only executable registry**: identical structural
  guarantee to Phase 09's check executor and Phase 08's command guard — the
  only way an executable path enters this runner is an operator-set
  environment variable parsed once at startup, never per-request input.
- **Fail-closed on unknown or disabled agents**, with an identical error
  message for both cases, so a caller cannot enumerate the registry's
  contents by probing which keys are "unknown" vs. "disabled."
- **Protocol version pinning enforced before any real work packet is
  sent** — a version-mismatched agent is refused at `initialize` time, not
  discovered mid-session.
- **No chain-of-thought exposure**: `acp-event-mapper.ts` never maps
  `agent_thought_chunk` updates to any `NormalizedRuntimeEvent` — verified
  by a dedicated test (`acp-event-mapper.test.ts`, "never emits
  agent_thought_chunk").
- **Two independent enable flags** (master + per-agent) — an operator can
  disable ACP globally or disable one bad agent without touching the other.
- Reuses Phase 08's already-hardened workspace isolation, path/command
  policy enforcement, and ephemeral-workspace disposal unchanged — this
  phase adds a second *transport* to reach an agent, not a second security
  model.

## 8. Remaining risks / next phase

- **No real ACP agent has been tested against this adapter** — only the
  protocol-shape-compatible fixture in `tests/fixtures/fake-acp-agent.mjs`.
  Before enabling this in any real environment, validate against an actual
  ACP-compliant agent binary and confirm its `session/update` shapes match
  `contracts/acp.ts`'s assumptions exactly.
- **No bridge yet** from the Python-side Celery job dispatcher into
  `apps/runner`'s ACP runtime, same gap already disclosed for the Phase 08
  OpenCode runtime (`opencode-sandbox.md` §6) — this phase registers ACP
  runtimes as *available*, not yet wired into live dispatch.
- **Resource isolation** (CPU/memory limits, true non-root enforcement) is
  a deployment-layer responsibility, identical to Phase 08's own disclosed
  limitation — this runner does not add container-level isolation for ACP
  agents beyond what Phase 08 already established.
