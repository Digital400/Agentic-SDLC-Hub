# Developer Local Agent Bridge (Phase 13)

**Status:** New, additive. New standalone TypeScript CLI package
(`apps/bridge`) and a new, disabled-by-default backend API surface
(`app/api/routes/developer_bridge.py`). Nothing in the existing company-
sandbox execution path (Phase 08 OpenCode, Phase 11 ACP) changed.

**Scope note, disclosed up front:** this phase's full requirement list is
large (13 CLI-side bullet points, 4 server-side bullet points). What's
built here is a genuinely working, tested core for every piece that this
environment could build and verify honestly. Two things are intentionally
stubbed rather than faked — see §5 — because completing them requires
integration points (a real signed-WorkPacket source, a real local runtime
binary) that don't yet exist in this environment; each stub fails loudly
with a comment pointing here, rather than fabricating success.

## 1. Why a separate CLI, not a desktop app

Phase 13 itself asks for "a lightweight TypeScript CLI/background process
rather than a large desktop application" — `apps/bridge` is a new Yarn/npm
workspace package (`@agentic-sdlc-hub/bridge`), following the exact same
"separately installable, its own package.json, its own local `npm install`"
precedent Phase 08's `apps/runner` already established
(`docs/architecture/opencode-sandbox.md` §3, "not yet Yarn-PnP-integrated").

## 2. The developer-side flow, and where each piece lives

| Requirement | Implementation |
|---|---|
| Authenticate via device authorization flow | `src/device-auth.ts` — the standard OAuth 2.0 Device Authorization Grant (RFC 8628) shape: request a device+user code pair, show the developer a URL/code, poll for approval |
| Register approved local runtimes and capability manifests | `src/runtime-registry.ts` — `BRIDGE_APPROVED_RUNTIMES_JSON`, an operator-set env var parsed once at startup; same "admin-approved-only, never per-request" structural guarantee as Phase 09/11's registries |
| Accept/reject assigned jobs | `src/orchestrator.ts`'s `runAssignedJob` calls `BridgeJobClient.acceptJob`/`rejectJob` |
| Fetch a short-lived signed WorkPacket | `BridgeJobClient.fetchSignedWorkPacket` (`src/job-client.ts`) |
| Verify repository remote, branch and base SHA | `src/repo-verify.ts`'s `verifyRepoState` — never clones (unlike the company sandbox's `git-clone.ts`); the developer's existing checkout is verified, not fetched |
| Launch an approved local ACP/native runtime | `RuntimeLauncher` interface in `src/orchestrator.ts` — see §5, not yet wired to a real process in this phase |
| Display tool/command approval requests locally | `src/approval-prompt.ts` — a real stdin prompt (`createStdinApprovalPrompter`), injectable for tests |
| Enforce allowed workspace and path boundaries | Reuses the same job-scoped repository verification (§ above); a real runtime launcher would additionally reuse `apps/runner`'s `assertPathAllowed` the same way Phase 11's ACP adapter does |
| Upload normalized events, patch hash, tests, usage | `BridgeJobClient.uploadEvidence` |
| Never upload the user's runtime credentials | `src/credential-guard.ts`'s `assertNoCredentialFields` — structural, word-boundary-aware key/value scanning, called before every evidence upload; refuses rather than silently strips |
| Never accept arbitrary commands from the platform | The platform never sends commands to the bridge at all in this design — it sends a WorkPacket (data) and the bridge's own approved local runtime interprets it, exactly mirroring the company sandbox's own "the platform never executes anything directly" model |
| Resume interrupted jobs | `src/job-state.ts`'s `JobStateStore` — a per-job JSON state file on disk, tracking phase (`accepted` → `repo_verified` → `running` → `uploading` → `completed`) |
| Expire job credentials after completion | `JobStateStore.loadIfCredentialValid` refuses and deletes state past `credentialExpiresAt`; `orchestrator.ts` also re-checks expiry immediately before upload, in case the local run itself took longer than the credential's lifetime |

## 3. The server side

`app/api/routes/developer_bridge.py` + `app/services/developer_bridge.py`,
gated entirely behind `Settings.DEVELOPER_BRIDGE_ENABLED` (default
`False` — every route 404s while disabled, identical pattern to
`STORY_GIT_PR_FLOW_V2_ENABLED`).

| Requirement | Implementation |
|---|---|
| Track connected/offline bridge status | `BridgeSession` model + `POST /bridge/status` |
| Treat local evidence as untrusted until CI verification | `BridgeJobAssignment.evidence_trusted` — a column that `upload_evidence()` never sets `True`; only a future CI integration may ever flip it |
| Reject evidence for the wrong repository or commit | `upload_evidence()` compares the claimed remote URL/commit against the ones recorded at assignment time and raises `BridgeJobWrongRepositoryError` on any mismatch — verified by two dedicated tests |
| Audit job assignment, acceptance and completion | Every mutation in `app/services/developer_bridge.py` calls `record_audit_log` (`bridge_device.approved`, `bridge_job.assigned`, `bridge_job.accepted`, `bridge_job.rejected`, `bridge_job.evidence_uploaded`) |

New tables (one Alembic migration, `988c3f86de35_developer_bridge_tables.py`):
`bridge_device_authorizations`, `bridge_sessions`, `bridge_job_assignments`.
All additive; no existing table changed.

## 4. Authentication model

There is still no session/JWT layer anywhere in this codebase
(`docs/architecture/universal-agent-runtime-baseline.md` §10 — "every
'who is doing this' check is driven by an explicit actor id present on
the request body"). The bridge's device-auth flow is the first place this
session introduces a real bearer token, scoped narrowly: `POST
/bridge/device/approve` still uses the existing explicit-actor-id pattern
(a developer's own already-authenticated web session posts
`{user_code, approved_by_user_id}`), and everything downstream
(`/bridge/status`, `/bridge/jobs/*`) is protected by the resulting opaque,
short-lived `access_token`, resolved back to a `User` by
`resolve_access_token()`. This token is scoped to the Developer Bridge API
surface only — it is not a general-purpose session token for the rest of
the application.

## 5. What's stubbed, and why — disclosed, not hidden

- **`GET /bridge/jobs/{id}/work-packet`** returns an honest placeholder
  (`{"packet": {...}, "signature": "unsigned-stub"}`) rather than a real
  signed `WorkPacket`. Building a real one requires wiring a
  `BridgeJobAssignment` to a real `ImplementationRun` and reusing
  `app/services/story_git_pr_flow.py`'s `build_work_packet_for_run` — a
  real integration point, not something to fabricate the shape of here.
- **`apps/bridge/src/cli.ts`'s `launchRuntime`** throws explicitly
  ("No RuntimeLauncher wired yet") instead of pretending to spawn a real
  ACP/native process. `orchestrator.ts`'s `RuntimeLauncher` interface and
  every other step around it (accept, verify, upload, expire) are fully
  implemented and tested with an injected fake launcher — only the real
  process-spawning implementation (which would reuse Phase 11's
  `JsonRpcConnection`/ACP contracts, or a native SDK per Phase 12's
  disclosed blocker) is not wired.

Both stubs are exercised end-to-end in tests via dependency injection
(`tests/orchestrator.test.ts`), so the *lifecycle* logic around them is
real and verified — only the two literal I/O boundaries named above are
placeholders.

## 6. Feature flags and rollback

- **`apps/bridge`'s own `BRIDGE_ENABLED`** (`src/config.ts`, default
  `false`) — the CLI refuses to run at all while unset.
- **Backend's `DEVELOPER_BRIDGE_ENABLED`** (`app/core/config.py`, default
  `false`) — every bridge route 404s while unset.

Both default `false`. Rollback: revert either to `false` — no data
migration; the new tables are additive and referenced by nothing else.

## 7. Tests executed and results

```
apps/bridge> npx tsc --noEmit   → 0 errors
apps/bridge> npx vitest run     → 6 test files, 30 tests, all passing
apps/api> pytest tests/test_developer_bridge.py -q  → 16 passed
apps/api> pytest -q (full suite)                     → 1044 passed
```

Two real bugs found and fixed via this test suite before completion:
- `credential-guard.ts`'s first regex-based key matcher (`\btoken\b`)
  still false-positived on `total_tokens` (a legitimate usage-metrics
  field) because `_` is a regex word character, so `\b` never separated
  "token" from "tokens" the way intended. Fixed by replacing the regex
  with an explicit snake_case/camelCase word splitter and an exact-word
  credential-term set (`credential-guard.ts`'s `splitIntoWords`/
  `keyLooksCredentialShaped`).
- `app/services/developer_bridge.py`'s expiry comparisons
  (`datetime.now(timezone.utc) <=`) crashed with "can't compare
  offset-naive and offset-aware datetimes" against this test suite's
  SQLite fixture, which returns naive datetimes for `DateTime(timezone=True)`
  columns even though Postgres round-trips them correctly. Fixed with a
  `_as_aware()` helper applied at every comparison site — a genuine
  SQLite-vs-Postgres portability gap this phase's own tests surfaced,
  not present in earlier phases only because none of them compared a
  read-back timestamp against "now" the way expiry logic requires.

## 8. Security considerations

- Credential-shaped fields are refused (not silently stripped) before any
  upload, both to catch a bug loudly and to avoid ever normalizing "we
  send this most of the time, minus secrets."
- The bridge access token is scoped narrowly to `/bridge/*` routes and
  short-lived (1 hour), independent of the underlying job credential's
  own TTL.
- `verifyRepoState` never trusts a server-supplied SHA without confirming
  the developer's local checkout actually contains it
  (`git cat-file -e <sha>^{commit}`).
- Server-side evidence is explicitly, permanently marked untrusted
  (`evidence_trusted=False`) until a real CI integration — not built in
  this phase — can verify it independently.

## 9. Remaining risks / next phase

- The two disclosed stubs in §5 must be completed before any real job can
  run through the bridge end to end.
- No Windows-specific installation/packaging (an actual installer, PATH
  registration, autostart) exists yet — only the CLI source and its
  `bin` entry in `package.json`. "Windows-first installation and
  development documentation" beyond this document itself is a follow-up.
- No real local runtime binary (ACP or native) has been tested against
  `RuntimeLauncher` — same disclosed gap as Phase 11's ACP adapter.
