# Developing a New Coding Runtime Adapter

**Audience:** an engineer adding support for a new coding agent/runtime
(a new vendor SDK, a new protocol, or a new local tool) to this
platform. Written as the "document runtime adapter development" deliverable
Phase 18 asks for, synthesized from the two real adapters this migration
already built and shipped: `apps/runner/src/runtime/opencode-adapter.ts`
(Phase 08, SDK-backed) and `apps/runner/src/runtime/acp-adapter.ts`
(Phase 11, protocol-backed).

## 1. Implement the `CodingRuntimeAdapter` interface

Defined in `apps/runner/src/contracts/coding-runtime-adapter.ts`. Every
adapter has exactly one required method:

```ts
interface CodingRuntimeAdapter {
  readonly definition: RuntimeDefinition;
  execute(request: {
    signedPacket: SignedWorkPacket;
    compiledContext: CompiledContext;
    onEvent: (e: NormalizedRuntimeEvent) => void;
  }): Promise<ExecutionResult>;
}
```

Your adapter's constructor builds `this.definition` — a
`RuntimeCapabilityManifest` describing what your runtime can do
(`RuntimeCapability` enum values), its execution location, and its
response formats. This is what `apps/api/app/coding_runtime/registry.py`'s
`RuntimePolicyEvaluator` reads to decide whether your runtime is even
eligible for a given task — get this right and honest; overclaiming a
capability your adapter doesn't actually enforce is a security gap, not
a convenience.

## 2. Follow the ten-step lifecycle every existing adapter follows

Both `OpenCodeRuntimeAdapter` and `AcpCodingRuntimeAdapter` implement the
identical ten steps (see `docs/architecture/opencode-sandbox.md` §1.2 and
`docs/architecture/acp-runtime.md` §4 for the full table). In order:

1. Verify the signed WorkPacket (`contracts/signed-work-packet.ts` —
   reuse this unchanged; never write a second signature scheme).
2. Create an isolated ephemeral workspace (`security/workspace.ts` —
   reuse `createEphemeralWorkspace`/`workspace.dispose()` unchanged).
3. Clone the repository at the immutable `base_commit_sha`
   (`runtime/git-clone.ts` — reuse `cloneAtBaseSha` unchanged; never trust
   a branch tip).
4. Apply the `ProjectExecutionProfile`'s allowed/denied commands and
   paths (`security/command-guard.ts`'s `assertCommandAllowed`/
   `assertPathAllowed` — reuse unchanged, `denyByDefault: true`).
5. Load only the already-compiled `CompiledContext` your adapter is
   handed — never re-derive or re-compile skills/policies yourself; that
   is `app.prompt_compiler`'s job, done before your adapter ever runs.
6. Route model access through the company model gateway, if your
   runtime's transport supports configuring one (see
   `security/permissions.ts`'s `buildOpenCodeConfig` for the pattern —
   fail closed if unconfigured, never silently fall back to a public
   provider).
7. Run your underlying tool/process with explicit, minimal permissions —
   no blanket allow, regardless of what a WorkPacket's own `ToolPolicy`
   claims.
8. Map your runtime's native event stream onto
   `contracts/events.ts`'s `NormalizedRuntimeEvent` vocabulary. **Never
   map a reasoning/chain-of-thought event to any normalized event** — see
   `runtime/acp-event-mapper.ts`'s explicit test for this exact rule.
9. Return the canonical `ExecutionResult` — file changes, commands run,
   test evidence, usage. Every `CommandEvidence`/`TestEvidence` must set
   `real_execution` honestly; never claim a real run for a
   reasoning-based assessment.
10. Dispose of the workspace in a `finally` block — always, even on
    failure or cancellation.

## 3. Register your adapter

Add one entry to `apps/runner/src/registry.ts`'s `buildRuntimeRegistry()`,
gated by its own feature flag (mirror `acpRuntimeEnabled`/
`openCodeRuntimeEnabled` in `src/config.ts`) defaulting to **disabled**.
If your runtime supports multiple configured instances (like ACP's
per-agent registry), add an admin-approved-only registry class mirroring
`security/acp-registry.ts` — **never accept an executable path or
command from a WorkPacket or any other per-request input**; the only
legitimate source is an operator-set environment variable parsed once at
startup.

## 4. Write the tests every adapter needs

- Unit tests for anything your adapter computes itself (event mapping,
  registry parsing) with no real process involved.
- A real integration test that spawns an actual child process/server your
  adapter talks to — a small fixture script is enough (see
  `apps/runner/tests/fixtures/fake-acp-agent.mjs`); this is what actually
  exercises your wire protocol, not a mock of your own adapter class.
- A security-refusal test: an unapproved executable/agent key must be
  refused before anything is ever spawned.
- Disclose, in your adapter's module docstring and in a new
  `docs/architecture/<your-runtime>.md`, exactly what has and hasn't been
  verified against a real instance of the runtime you're integrating —
  every adapter in this codebase so far has needed this disclosure
  (OpenCode: real SDK, no real production account tested; ACP: real
  wire protocol, no real ACP-compliant agent binary tested).

## 5. Windows-specific gotcha this migration hit twice

A just-killed child process can briefly continue holding its working
directory on Windows even after Node's `"close"` event fires, so a bare
`rm(workspaceDir, { recursive: true, force: true })` immediately after
disposal can throw `EBUSY`. Fixed at both call sites this migration
found it (`check-executor.test.ts`'s own cleanup, and
`security/workspace.ts`'s `dispose()` itself) with
`maxRetries: 5, retryDelay: 200`. If your adapter spawns a raw child
process the way `AcpCodingRuntimeAdapter` does, expect this and don't
remove the retry from `workspace.ts`.
