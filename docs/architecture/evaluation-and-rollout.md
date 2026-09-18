# Evaluation, Canary Rollout and Cleanup (Phase 18)

**Status:** Partial, explicitly disclosed. This is the final phase of the
19-phase Universal Agent Runtime Baseline migration, and it is the
second phase in this migration (after Phase 12) that names requirements
this development session genuinely cannot complete honestly — not
because they're hard to code, but because they require things that
literally cannot exist inside one working session: real elapsed
calendar time, real production traffic, and a real security review by
people, not by this assistant.

## 1. What Phase 18 asks for, and what's genuinely achievable now

Phase 18's own text asks for, in order:

1. An evaluation harness comparing legacy vs. new runtimes on 14 named
   metrics, using "the golden dataset and redacted historical approved
   examples."
2. An 8-stage rollout (offline evaluation → shadow → internal admins →
   selected developers → one pilot project → 25% → 50% → 100%).
3. Independent rollback switches for 8 named systems.
4. A rule: never remove a legacy path until quality gates pass, security
   review passes, rollback is tested, audit records remain accessible,
   **and two stable release cycles complete.**
5. Finally: remove unreachable legacy code, reconcile documentation,
   document runtime adapter development and incident response, and run
   every test suite.

**Genuinely built in this pass:** (1) the evaluation harness itself, (3)
every rollback switch (all already existed, one per phase that built the
system it gates — this phase adds a single honest status-reporting
function reading them back), and the two documentation deliverables from
(5) — `docs/architecture/runtime-adapter-development.md` and
`docs/architecture/incident-response-and-sandbox-cleanup.md`.

**Not built, and disclosed rather than faked:** (2) stages 3 through 8 of
the rollout, (4) the "two stable release cycles" gate, and the "remove
unreachable legacy code" step in (5). See §3 for exactly why.

## 2. The evaluation harness

`app/services/runtime_evaluation.py` — a real, tested comparison engine:
`run_evaluation(cases, legacy_runner, new_runner)` runs both runners
against every `EvaluationCase` and an `EvaluationReport` computes all 14
named metrics (schema validity, required-section completeness,
clarification rate, latency, tokens, cost, cache savings, tool failures,
permission violations, patch acceptance, test/CI success — plus
human-acceptance-without-edits and PR-review-usefulness, both of which
correctly return `None` until a real human judgement is recorded, never
a fabricated default).

**No golden dataset ships with this phase.** `.sdlc/evaluation/golden/`
is real and documented (`README.md` inside it) but contains zero cases.
Curating real, redacted historical approved examples requires a real
anonymization pipeline over real production history this development
environment has no access to — inventing synthetic "historical" examples
and presenting them as real would be exactly the kind of fabrication
this entire migration has committed to disclosing rather than doing.
`load_golden_cases()` is ready to read real cases the moment a human
curates them.

## 3. Why the rollout stages, "two release cycles," and legacy removal are not done

This is not a scope-reduction of convenience — it is a structural
impossibility, disclosed as one from the very first phase-planning
conversation of this migration:

- **"One pilot project," "25%/50%/100% of eligible runs"** require real
  production deployment and real user traffic. Every phase in this
  migration was built and merged within a single continuous working
  session against a local development checkout — there is no production
  deployment of any of it, and therefore no traffic to roll out to.
  `app/services/runtime_rollout.py`'s `current_rollout_status()` reports
  every system's real, current, honest state: at or before
  `OFFLINE_EVALUATION`, several (Native premium adapters) at
  `NOT_STARTED`. This is not a placeholder value — it is the true state
  of a system that has never been deployed.
- **"Two stable release cycles"** requires real elapsed calendar time
  after a real deployment — something that cannot be simulated,
  accelerated, or asserted true by writing code. No release has
  happened, so no release cycle has started, so two cannot have
  completed.
- **"Security review passes"** requires a real human security reviewer's
  sign-off — an assistant cannot grant this to its own work regardless
  of how much self-review it performs. The security considerations
  sections in every one of this migration's phase docs are that
  self-review; they are not a substitute for an independent one.
- **"Remove unreachable legacy code"** is explicitly gated on all of the
  above by Phase 18's own instructions ("do not remove a legacy path
  until... two stable release cycles complete"). Since none of the
  gating conditions are met, no legacy code is removed in this phase.
  Every legacy path this migration touched — `ai_generation.generate()`,
  `create_pull_request`, the project-level workflow graph — remains
  fully intact and is still what every real, unflagged call path uses
  today, exactly as every individual phase's own "Feature flag and
  rollback procedure" section already promised.

## 4. Documentation reconciliation

- `docs/architecture/runtime-adapter-development.md` (new) — how to add
  a new `CodingRuntimeAdapter`, synthesized from the two real ones this
  migration built (OpenCode, ACP).
- `docs/architecture/incident-response-and-sandbox-cleanup.md` (new) —
  rollback switches, what a misbehaving runtime can/cannot do, workspace
  cleanup, and the audit trail to check first.
- This document itself supersedes no other phase document — every prior
  phase's own `docs/architecture/*.md` file remains the authoritative
  record for that phase; nothing here should be read as retracting a
  claim any of them made.

## 5. Tests executed and results — the final "run all tests" step

```
apps/api> pytest -q (full suite, including this phase's 17 new tests)
  -> 1113 passed
apps/web> yarn vitest run
  -> 4 files, 39 tests, all passing
apps/web> yarn exec tsc --noEmit -> 0 errors
apps/web> yarn build -> compiles, all routes generate successfully
apps/runner> yarn workspace @agentic-sdlc-hub/runner typecheck -> 0 errors
apps/runner> yarn workspace @agentic-sdlc-hub/runner test -> 100 passed
apps/bridge> npx tsc --noEmit -> 0 errors
apps/bridge> npx vitest run -> 30 passed
```

No "integration and end-to-end" browser suite exists to run (disclosed
in Phase 14's own doc — no Playwright suite was added), and no live
staging/production environment exists to run a real end-to-end smoke
test against. Every automated test this migration's tooling can run,
across all four packages (`apps/api`, `apps/web`, `apps/runner`,
`apps/bridge`), passes.

## 6. Summary of this migration's actual, honest end state

Every one of the 19 planned phases has now been addressed:

- **00, 01, 03-11, 13-17**: fully built, tested, and merged.
- **02**: backfilled mid-migration after being found skipped.
- **12**: disclosed blocker (no vendor SDK access) — analysis document
  in place of a native adapter.
- **18 (this phase)**: partially built (evaluation harness, rollback
  status reporting, two documentation deliverables) with the
  time/traffic-dependent remainder explicitly disclosed as not
  completable inside a development session, per §3.

Every feature flag added across all 19 phases remains at its safe,
disabled default. The legacy path for every single system this
migration touched is fully intact and is what every real, unflagged
call in this codebase still uses today.
