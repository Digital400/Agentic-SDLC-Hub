# Golden evaluation dataset — Phase 00 baseline

Each `NN-*.json` file is a **redacted** fixture describing one scenario: the inputs that would
drive it through the current runtime, which code path handles it (file:line citations back to
`../universal-agent-runtime-baseline.md`), and the expected shape of the outcome **as the current
code actually behaves** — not an aspirational target.

## Redaction rules applied

- No real API keys, tokens, or credentials appear anywhere in these fixtures. Every token/key
  field is the literal string `"REDACTED"`.
- No real customer/project data. All project names, story titles, and file paths are synthetic.
- No chain-of-thought or internal reasoning is captured or reproduced (the runtime does not
  expose it — see baseline doc §10 — so there is none to redact).

## What "expected" means per scenario

- Where the current codebase is **deterministic** (the mock/heuristic fallback paths), the
  `expected` block is a precise, verifiable description of that deterministic behavior — these
  fixtures can be turned into real regression assertions later without further fabrication.
- Where the current codebase depends on a **real provider's output** (an actual Claude/Gemini/etc.
  response), the `expected` block describes the *contract* the code enforces on that output
  (schema, allowed enum values, honesty rules) rather than fabricating specific model text. Any
  numeric metric that would only be known from a real call (latency, exact token count, exact
  cost) is marked `"UNKNOWN"` per the phase's instruction not to fabricate missing metrics.

## Scenario index

| File | Scenario | Primary code path |
|---|---|---|
| `01-successful-artifact-generation.json` | Successful artifact generation | `ai_generation.generate()` → mock path, loop stops `COMPLETED_QUALITY_MET` |
| `02-clarification-required.json` | Clarification required | `ai_generation._parse_response` → `CLARIFICATION_MARKER`, loop stops `WAITING_FOR_INPUT` |
| `03-invalid-model-output.json` | Invalid model output | `_parse_response` malformed-header fallback; structured-agent `json.JSONDecodeError` fallback |
| `04-validation-and-repair.json` | Validation and repair | `LoopEngineService.run_loop` IMPROVE cycle |
| `05-vertical-story-generation.json` | Vertical story generation | `StoryType.VERTICAL`, `story_crafting` prompt `**Mode:** VERTICAL` |
| `06-horizontal-story-generation.json` | Horizontal story generation | `StoryType.HORIZONTAL`, `story_crafting` prompt `**Mode:** HORIZONTAL` |
| `07-story-specific-lld.json` | Story-specific LLD | `story_lld_agent.py`, transient `WorkflowNode`, `StoryArtifact` output |
| `08-provider-failure.json` | Provider failure | `AIGenerationError` → `agent_runs.py` `_fail()` / structured-agent heuristic fallback |
| `09-budget-limit.json` | Budget limit | `TokenBudgetService.build()` compress/drop behavior — **no spend-limit enforcement exists** (documented gap, not fabricated) |
| `10-implementation-proposal.json` | Implementation proposal | `implementation_agent.run_implementation_agent()`, diff + `after_content`, human accept/reject gate |
| `11-testing-passing.json` | Passing tests | `testing_agent.py` reasoning-based assessment, `tests_executed` all `PASS` |
| `12-testing-failing.json` | Failing tests | same path, `tests_executed` containing `FAIL`, `bugs_found` populated |
| `13-stale-pr-review.json` | Stale PR review | **No staleness detection exists** — fixture documents the gap, not a fabricated feature |
