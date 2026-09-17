# OpenCode Company-Managed Sandbox (Phase 08)

**Status:** New, additive, standalone TypeScript service (`apps/runner`),
registered as an available-but-disabled coding runtime. Nothing in the
existing FastAPI backend's request path, database schema (beyond one new
boolean setting), or business workflow changed.

## 1. What this phase built

`apps/runner` — a new `@agentic-sdlc-hub/runner` workspace, TypeScript,
using the real, verified `@opencode-ai/sdk@1.18.31` (installed and
inspected directly — every method call in this phase's code matches a real
type declaration, not a guessed API shape).

### 1.1 The `CodingRuntimeAdapter` contract

This phase's instructions named "the existing CodingRuntimeAdapter
contract" — no such contract existed anywhere in this codebase before this
phase (confirmed by inspection). Following this session's established
strangler-migration precedent (Phase 05 defined `ModelGateway` the same
way; Phase 06 defined `AgentJobDispatcher` the same way), this phase
defines it fresh: `apps/runner/src/contracts/coding-runtime-adapter.ts`.
Exactly one implementation satisfies it today — `OpenCodeRuntimeAdapter`
(`apps/runner/src/runtime/opencode-adapter.ts`) — leaving room for a second
adapter later without touching any caller of the interface.

### 1.2 The ten steps, and where each lives

| # | Step | Implementation |
|---|------|-----------------|
| 1 | Receive a signed WorkPacket | `contracts/signed-work-packet.ts` — HMAC-SHA256 over canonical JSON, keyed by `RUNNER_SHARED_SIGNING_SECRET`. Verification failure never reaches OpenCode. |
| 2 | Isolated ephemeral workspace | `security/workspace.ts` — a fresh, randomly-named, mode-0700 directory per job under `RUNNER_WORKSPACE_ROOT`; refuses to run as root (uid 0) at creation time. |
| 3 | Clone at immutable base SHA | `runtime/git-clone.ts` — clones `base_branch`, then explicitly fetches and `checkout --detach`s `base_commit_sha`; never trusts the branch tip. |
| 4 | Apply approved ProjectExecutionProfile | The `SignedWorkPacket.execution_profile` field (a TS mirror of `ProjectExecutionProfile`'s runtime-relevant columns) drives the allowed/denied commands, allowed/denied paths, and skill requirements below. |
| 5 | Compile and load only applicable skills/context | `CompiledContext` (already-rendered Phase 04 PromptCompiler output) is rendered as the session's leading prompt — this runner never re-derives or re-compiles skills itself. |
| 6 | Route model access through the company model gateway | `security/permissions.ts`'s `buildOpenCodeConfig` configures OpenCode's `provider.company-model-gateway.options.baseURL`/`apiKey` from `MODEL_GATEWAY_BASE_URL`/`MODEL_GATEWAY_API_KEY` — **fails closed**: refuses to start OpenCode at all if unconfigured, never falls back to a public provider. |
| 7 | Run OpenCode with explicit permissions | Same `buildOpenCodeConfig` — see section 2 below for the full permission mapping. |
| 8 | Stream normalized events | `runtime/event-mapper.ts` maps OpenCode's real `Event` union onto Phase 06's exact `AgentJobEventType` vocabulary (`contracts/events.ts`), subscribed via the real `client.global.event()` SSE stream. |
| 9 | Return patch, changed files, commands, test evidence, usage | The returned `ExecutionResult` — a TS mirror of `app/agent_runtime/execution_result.py`, field-for-field. Every `CommandEvidence`/`TestEvidence` is `real_execution: true` (a real OpenCode server actually ran them) — never fabricated. |
| 10 | Dispose of the workspace | `workspace.dispose()`, called in a `finally` block — always runs, even on failure/cancellation. |

### 1.3 Security controls — what's real vs. deployment-layer

This runner is honest (mandatory platform rule, applied here to security
claims the same way `execution_result.py`'s `real_execution` field applies
it to execution claims) about what a plain Node.js process can and cannot
hermetically guarantee:

**Enforced by this runner's own code today:**
- Non-root execution refusal (`process.getuid() === 0` check; POSIX only)
- Ephemeral, per-job workspace with a disk-usage cap enforced by polling
- A hard wall-clock ceiling that aborts a run, independent of any
  WorkPacket-supplied budget (`BudgetTracker`, `security/limits.ts`)
- Token/cost/tool-call/distinct-tool budget enforcement, checked as usage
  streams in, not only after the fact
- Network denied by default (`permission.webfetch: "deny"`)
- No MCP servers configured at all in this phase (`mcp: {}`)
- A fixed, closed denylist of destructive command shapes
  (`rm -rf /`, `sudo`, fork bombs, pipe-to-shell, `git push`, ...), checked
  **before** anything reaches OpenCode's own `permission.bash` map — defense
  in depth, never trusting OpenCode's own config alone
- `permission.bash` is never a blanket `"allow"` — every command is either
  one of `ProjectExecutionProfile`'s own approved commands (explicit
  `"allow"`) or denied
- Path allowlist/denylist enforcement (`assertPathAllowed`) — absolute
  paths, `~`, Windows drive letters, and `..` traversal are all refused
  regardless of `ProjectExecutionProfile.allowed_paths`
- No platform secret mounts: `environment_variable_names` (names only,
  never values — same HARD RULE as the Python column) is the only
  environment surface passed through; this process's own env is never
  copied wholesale into OpenCode's config
- Never pushes code: `git push` is on the fixed denylist, and no push/PR
  code path exists anywhere in this phase's adapter

**NOT hermetically enforced by this runner alone — a deployment-layer
responsibility (deploy inside a locked-down, non-privileged container):**
- True kernel-level CPU/memory isolation (cgroups)
- Syscall filtering / true non-root enforcement beyond a uid==0 refusal
  (a compromised non-root process can still exhaust host resources)
- A guaranteed-unreachable host filesystem (this runner never *intends* to
  touch anything outside the workspace directory, but only a container
  boundary makes that a hard guarantee)

## 2. Feature flags

Two independent flags — apps/runner is a standalone process the FastAPI
backend does not import, so its own flag and the backend's registration
flag are deliberately separate, not aliases of each other:

- **`apps/runner`'s own `OPENCODE_RUNTIME_ENABLED`** (`apps/runner/src/config.ts`,
  default `false`) — the actual runtime gate: `getEnabledAdapter()` refuses
  to hand out a runnable `OpenCodeRuntimeAdapter` unless this is `true`.
- **Backend's `Settings.OPENCODE_RUNTIME_ENABLED`** (`apps/api/app/core/config.py`,
  default `false`) — purely a registration/visibility flag, read by the new
  `app/services/coding_runtimes.py`'s `build_coding_runtime_registry()`
  (mirrors `apps/runner/src/registry.ts`'s `buildRuntimeRegistry` — two
  independent, symmetric declarations, since the two processes never
  import each other's code). `"opencode"` is always listed in the
  registry, `enabled` reflects this flag — an operator/future UI can always
  see the runtime exists and why it's off.

Both remain `false` by default. Enabling either alone changes nothing
observable: the backend flag only affects what a future registry-reading
caller sees; the runner flag only affects what `apps/runner`'s own
`getEnabledAdapter()` will hand out — and nothing in this phase wires
`apps/runner` into the Celery dispatch path at all (see section 4).

**Rollback:** revert both flags to `false` (their defaults) — no data
migration, no schema change beyond the one new boolean column-free setting
field.

## 3. Files changed

- **New:** `apps/runner/` (entire new workspace — `package.json`,
  `tsconfig.json`, `vitest.config.ts`, `src/`, `tests/`)
- **New:** `apps/api/app/services/coding_runtimes.py`
- **New:** `apps/api/tests/test_coding_runtimes_registry.py`
- **Modified:** `package.json` (root) — added `"apps/runner"` to `workspaces`
- **Modified:** `apps/api/app/core/config.py` — added `OPENCODE_RUNTIME_ENABLED: bool = False`

No Alembic migration — no database schema changed.

## 4. Tests executed and results

**apps/runner** (`vitest`, via a locally-scoped `npm install` — this
workspace's dependencies were installed independently of the monorepo's
Yarn PnP root install to avoid disturbing the existing Yarn-managed
workspaces; a real Yarn-integrated install is a follow-up, not required by
this phase's own scope):

```
apps/runner> npx tsc --noEmit        → 0 errors
apps/runner> npx vitest run          → 8 test files, 47 tests, all passing
```

Test files: `signed-work-packet.test.ts` (4), `command-guard.test.ts` (14),
`permissions.test.ts` (6), `event-mapper.test.ts` (9), `limits.test.ts` (5),
`workspace.test.ts` (3), `registry.test.ts` (5), and the **fixture-repository
integration test** `git-clone.integration.test.ts` (1) — builds a real,
local two-commit git repository and proves `cloneAtBaseSha` checks out the
tree exactly as it was at the pinned first commit even after the fixture
repo's branch tip moved forward to a second commit.

Two real bugs were found and fixed via this test suite before completion:
- `config.ts`'s `envFlag` read `process.env` directly instead of the
  `env` parameter actually passed to it — meaning `loadRunnerConfig(env)`
  silently ignored any injected/overridden environment for the
  `OPENCODE_RUNTIME_ENABLED` flag specifically (every other field already
  read from the parameter correctly). Fixed to thread `env` through.
- The wall-clock-ceiling test was flaky (elapsed time could read exactly
  `0` within the same JS tick) — fixed by adding a measurable delay in the
  test, not by weakening the production comparison.

**apps/api** (`pytest`): the new `test_coding_runtimes_registry.py` (3
tests) passes when run in isolation from this machine's local `.env` drift
(see below); a pre-existing, unrelated local environment issue —
`.env` on this machine contains `HUGGINGFACE_API_KEY`/`HUGGINGFACE_MODEL`
values not declared as fields on `Settings`, which fails **every** test
that constructs `Settings` via `get_settings()`, including several
pre-existing Phase 07 test files (`test_runtime_security_security_gate.py`
included) — confirmed by reproducing the identical failure on that
pre-existing, unmodified file. This is not a regression introduced by this
phase; it was not fixed here, since doing so would mean editing a
developer's local `.env` (which contains what looks like a live API key)
or changing `Settings`'s schema for an unrelated integration outside this
phase's scope. Verified correct behavior of the new module directly by
temporarily stripping just those two lines from `.env` for one local test
run and restoring the file byte-for-byte immediately after (diffed clean).

## 5. Security considerations

- The model gateway routing is **fail-closed**: `buildOpenCodeConfig`
  throws `ModelGatewayNotConfiguredError` rather than let OpenCode start
  with any directly-configured public provider.
- `permission.bash` is never a blanket allow, regardless of what a
  WorkPacket's own `ToolPolicy` claims — defense in depth against a
  compromised or malformed upstream packet.
- Signature verification uses `timingSafeEqual`, not `===`, avoiding a
  timing side-channel on the HMAC comparison.
- `git push` is unconditionally denied at the command-guard layer,
  independent of any `ProjectExecutionProfile` configuration — "never push
  code in this phase" is enforced by the fixed platform denylist, not left
  to policy configuration alone.
- Reasoning/chain-of-thought message parts are explicitly never mapped to
  any normalized event (`event-mapper.ts`) — mirrors Phase 06's
  `scrub_chain_of_thought` and the platform-wide "never expose
  chain-of-thought" rule.

## 6. Remaining risks / next phase

- **No bridge exists yet** between Phase 06's `CeleryJobDispatcher`
  (Python) and `apps/runner` (TypeScript) — this phase registers OpenCode
  as *available*, per its own literal instructions, but does not wire it
  into the actual job-dispatch path. A future phase would add an HTTP (or
  queue-based) bridge: the Celery worker POSTs a `SignedWorkPacket` to
  `apps/runner`'s (not-yet-built) HTTP entrypoint and relays its streamed
  events back into `AgentJobEvent` rows.
- **Dependency install is not yet Yarn-PnP-integrated** — `apps/runner`
  was installed via a locally-scoped `npm install --no-workspaces` to
  avoid disturbing the existing Yarn-managed root install during this
  phase; integrating it into the Yarn PnP workspace properly (likely via a
  `packageExtensions` entry for `@opencode-ai/sdk`, mirroring the existing
  `eslint-config-next` extension) is a follow-up.
- **Real resource-limit enforcement** (CPU/memory cgroups, true non-root
  isolation beyond a uid check) requires a container runtime at the
  deployment layer — documented explicitly in section 1.3, not silently
  assumed.
- **No repository-credential issuance path** is wired from Phase 07's
  `RuntimeCredentialBroker` into `apps/runner` yet — `repoAccessToken` is
  accepted as a constructor option for a future caller to supply, but
  nothing in this phase calls the Python-side broker to obtain one.
