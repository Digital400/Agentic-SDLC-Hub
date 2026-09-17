# Universal Agent Runtime Baseline (Phase 00)

**Status:** Documentation only. No production behavior was changed to produce this document.
**Scope:** Read-only inspection of the current agent runtime — generation, budgeting, context
assembly, retrieval, the loop engine, the graph engine, story delivery lanes, the
implementation/testing/PR-review agents, and the external integrations they depend on.
**Method:** Direct source inspection (file:line citations throughout) plus one full run of the
existing backend test suite. Nothing below is inferred from naming alone unless explicitly
flagged; unknown/unmeasured values are marked `UNKNOWN` rather than estimated.

---

## 0. Repository orientation

- Backend: FastAPI + SQLAlchemy, `apps/api/app/`, Alembic migrations in `apps/api/alembic/versions/`.
- Frontend: Next.js/TypeScript, `apps/web/`.
- Test suite: `apps/api/tests/` (pytest), run against a SQLite/in-memory or local Postgres fixture
  session (see `apps/api/tests/conftest.py` — not modified or further inspected beyond what was
  needed to run the suite).
- No authentication/session layer exists yet. Every "who is doing this" check in the backend is
  driven by an explicit actor id (`triggered_by_user_id`, `created_by_id`, `reviewer_id`, …) present
  on the request body itself, checked against that user's stored `role` — see §10.

---

## 1. Current agent call paths

There are two distinct call paths into `app/services/ai_generation.py`'s `generate()` /
`generate_raw_text()`, and one path that is **defined but not wired into any route**.

### 1.1 Project-level / story-LLD DRAFT runs (looped)

```
POST /agent-runs
  app/api/routes/agent_runs.py: start_agent_run()
    -> GraphEngineService.validate_can_run()         [app/services/graph_engine.py]
    -> retrieve_relevant_chunks()                     [app/services/retrieval.py]
    -> fetch_recent_review_comments()                 [app/services/context_builder.py]
    -> LoopEngineService.run_loop()                    [app/services/loop_engine.py]   (action == DRAFT only)
         loop: generate() -> run_validator() -> (IMPROVE -> generate() -> run_validator())*
    -> ai_generation.generate()                        [app/services/ai_generation.py]  (called once per loop iteration)
         -> build_prioritized_context() -> TokenBudgetService.build()
         -> get_active_provider()
         -> _generate_with_<provider>() | mock_agent.generate_mock_output()
```
(`apps/api/app/api/routes/agent_runs.py:130-378`, `apps/api/app/services/loop_engine.py:130-325`,
`apps/api/app/services/ai_generation.py:694-807`)

### 1.2 Project-level VALIDATE / IMPROVE runs (single-shot, not looped)

Same route (`start_agent_run`), same graph-rule gate, but calls `ai_generation.generate()`
directly instead of going through `LoopEngineService` — "a VALIDATE or IMPROVE run is already a
single well-defined human-triggered agent step, not a thing to loop"
(`apps/api/app/services/loop_engine.py:41-44`, route branch at
`apps/api/app/api/routes/agent_runs.py:280-343`).

### 1.3 Story-scoped LLD (`story_lld_agent.py`)

Reuses the exact same `ai_generation.generate()` entry point by constructing a **transient,
never-persisted** `WorkflowNode` object as the `node` parameter, since a story-lane stage has no
row in the project-level `workflow_nodes` table
(`apps/api/app/services/story_lld_agent.py:1-19,41`). Same provider fallback chain, same P0-P5
prioritization, same two-part draft/clarification contract as §1.1/1.2 — this is **not** a
separate generation implementation.

### 1.4 Structured JSON agents (Implementation / Testing / PR-Review)

These three do **not** use `generate()` (which is built around the draft/clarification Markdown
contract). They call the lower-level `generate_raw_text()` directly with their own system prompt
and parse the response as JSON themselves:

```
implementation_agent.run_implementation_agent()  -> generate_raw_text() -> json.loads(...)
testing_agent.run_testing_agent()                 -> generate_raw_text() -> json.loads(...)
pr_review_agent.run_pr_review_agent()              -> generate_raw_text() -> json.loads(...)
```
(`apps/api/app/services/implementation_agent.py:230-301`,
`apps/api/app/services/testing_agent.py:158-206`,
`apps/api/app/services/pr_review_agent.py:177-237`)

All three share one resilience contract: if `get_active_provider() == "mock"`, call the
deterministic heuristic builder directly; otherwise try the real call, and on
`AIGenerationError | json.JSONDecodeError | ValueError | TypeError | AttributeError | KeyError`,
log a warning and **fall back to the same heuristic builder** rather than raising
(`implementation_agent.py:307-335`, `testing_agent.py:212-234`, `pr_review_agent.py:243-269`).
None of the three ever writes to a real repository, branch, or GitHub PR themselves — they only
produce data a route persists (see §7-9).

### 1.5 `generate_raw_text` — the shared minimal completion primitive

`app/services/ai_generation.py:810-831`. No draft/clarification contract, no context-budget
assembly — a bare system-prompt + user-content completion via whichever provider
`get_active_provider()` currently resolves to. Used by §1.4's three agents,
`app/services/validator_agent.py`'s real-AI validator path, and
`app/services/artifact_summary.py`'s real-AI summarization path (found via `generate_raw_text`
callers; artifact_summary.py itself was not opened in this pass — flagged for completeness, not
verified line-by-line).

### 1.6 Defined-but-unused path: `ContextBuilderService`

`app/services/context_builder.py`'s `ContextBuilderService.build()` (an eight-step pipeline,
class docstring lines 1-42) is a **structurally separate reimplementation** of the same
P0-P4/P5 priority-and-budget logic that `ai_generation.build_prioritized_context` already
performs for real runs. Verified by import search:

- `ContextBuilderService` itself is imported only by `apps/api/tests/test_context_builder.py`.
- `apps/api/app/api/routes/agent_runs.py` imports only the module-level helper function
  `fetch_recent_review_comments` from `context_builder.py` — never the `ContextBuilderService`
  class.

**No production route calls `ContextBuilderService.build()`.** Its own docstring frames it as
"meant to be callable on its own — e.g. a 'preview what the agent will actually see' endpoint" —
that endpoint does not exist yet. This is the clearest concrete instance of "current context
construction and duplication" (§4).

---

## 2. Current provider-selection path

`app/services/ai_generation.py:get_active_provider()` (lines 163-202), called on the hot path of
every single agent-run/Approve request. Priority order, first match wins:

| Order | Provider | Selected when | Extra check |
|---|---|---|---|
| 1 | `anthropic` | `ANTHROPIC_API_KEY` set | none |
| 2 | `gemini` | `GEMINI_API_KEY` set | none |
| 3 | `openrouter` | `OPENROUTER_API_KEY` set | `_openai_compatible_endpoint_is_reachable()` — bounded 5s GET to `{base}/models` |
| 4 | `nvidia` | `NVIDIA_API_KEY` set | same reachability check, 5s bound |
| 5 | `ollama` | none required | `ollama.Client(...).list()` succeeds within 5s |
| 6 | `mock` | fallback | — |

There is **no per-request or per-agent-definition provider override** — provider selection is a
single process-wide function of environment configuration, evaluated fresh on every call (no
caching of the resolved provider across requests). `AgentDefinition.model_name` exists in the
schema (see §3) but is **not read anywhere in the provider-selection or generation path** — it is
a placeholder field, confirmed by grep: no reference to `model_name` outside its own model
definition and schema/seed files was found in `ai_generation.py`, `loop_engine.py`, or any route.

The reachability checks exist specifically because NVIDIA's hosted endpoint has been observed in
practice to accept a connection and never respond at all — "not hypothetical"
(`ai_generation.py:105-114,168-174`).

---

## 3. Existing model and prompt configuration

### 3.1 Model selection — config-driven, not DB-driven

Actual model IDs come from `app/core/config.py` (`Settings`), one env var per provider:

- `AI_MODEL` (Anthropic) — default `claude-opus-5` (`config.py:44`)
- `GEMINI_MODEL`, `OPENROUTER_MODEL` (default `minimax/minimax-m3:free`), `NVIDIA_MODEL` (default
  `moonshotai/kimi-k3`), `OLLAMA_MODEL` (default `llama3.1:8b`)

`_MODEL_PRICING_PER_MTOK` (`ai_generation.py:77-81`) hardcodes per-1M-token pricing for exactly
three Anthropic model names (`claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5`). Any other
`AI_MODEL` value falls back to `(0.0, 0.0)` — cost silently reports as `$0`. Gemini/OpenRouter/
NVIDIA/Ollama always report `cost=0.0` by explicit design (free-tier framing), not because they
were priced and came out to zero.

### 3.2 Prompt content — DB-driven

`AgentDefinition` / `AgentPrompt` (`apps/api/app/models/agent.py:13-76`) are the real prompt
store: one `AgentDefinition` per agent key, versioned `AgentPrompt` rows per
`(agent_definition, role)` with exactly one `is_active=True` per pair. `role` is one of
`AgentPromptRole.DRAFT | IMPROVE | VALIDATE` (`enums.py:77-82`). `_build_system_prompt()`
(`ai_generation.py:399-421`) assembles the final system prompt from `active_prompt.system_prompt`
+ `output_format` + `validation_checklist`, plus a fixed citation/response-format suffix that is
identical across every agent (hardcoded in this function, not configurable per-agent).

### 3.3 Generation parameters — mostly hardcoded, budgets are DB-driven

- `temperature`/sampling params: hardcoded per provider function. Anthropic uses
  `thinking={"type": "adaptive"}` with no explicit temperature (`ai_generation.py:456-462`).
  Ollama hardcodes `temperature=0.7, top_p=0.9, top_k=40, repeat_penalty=1.1, num_thread=8,
  num_ctx=4096` (`ai_generation.py:656-664`) — explicitly framed as a CPU-inference speed
  optimization, not a quality choice.
- `max_tokens`/`output_token_budget`: **is** DB-driven — comes from
  `WorkflowNode.context_token_budget` / `WorkflowNode.output_token_budget`
  (`apps/api/app/models/workflow.py`, referenced at `ai_generation.py:718-722`), set per workflow
  stage, passed through on every call including the mock path.

---

## 4. Current context construction and duplication

Two independent, parallel implementations of "assemble this run's context" exist:

1. **`ai_generation.build_prioritized_context()`** (`ai_generation.py:223-396`) — the one actually
   used by every real generation call (§1.1-1.3). Produces one flat prompt string via
   `TokenBudgetService`.
2. **`ContextBuilderService.build()`** (`context_builder.py:112-289`) — an eight-step pipeline
   producing a structured, per-field result (`ContextBuilderResult`), built on the *same*
   `ContextBlock`/`TokenBudgetService` primitives but as an entirely separate call graph. Confirmed
   dead in production (§1.6) — its only real caller is its own test file.

Both apply materially the same priority scheme (P0 instruction/stage-rules → P1 project/node
rules → P2 approved-artifact summaries, escalating to full content under the same
`full_content_artifact_types` / `force_full_content` rule → P3 RAG chunks → P4 review comments;
`build_prioritized_context` additionally has a P5 tier context_builder.py's summary/full-content
escalation folds into P2). The duplication is structural (two code paths implementing the same
rule set) rather than a duplication of content *within* a single prompt — no evidence was found of
the same content block being inserted twice into one actual LLM call.

A second, unrelated context-assembly path exists for implementation/testing/PR-review:
`RepoContextBuilderService` (`app/services/repo_context_builder.py`) builds *repository file*
context (as opposed to artifact/RAG context) and **is** wired into production
(`implementation_runs.py:296`, `projects.py:575`). It was not deep-read in this pass beyond
confirming its production usage — recommended for closer review in a later phase if repo-context
budgeting is in scope.

Token estimation itself (`token_budget.py:estimate_tokens`) is a fixed 4-characters-per-token
heuristic, not a real tokenizer for any of the five providers — explicitly documented as "good
enough to keep a prompt roughly within budget, not an exact accounting of what the provider will
actually charge" (`token_budget.py:1-12`). This means `AgentRun.estimated_context_tokens` and
`AgentRun.token_usage.prompt_tokens` (the provider's real reported count) are expected to diverge,
and the codebase already frames that divergence as an intentional signal, not a bug
(`agent.py:128-138`).

---

## 5. Current token and cost recording

Recorded per run on `AgentRun` (`apps/api/app/models/agent.py:126-150`):

- `token_usage: dict | None` — `{prompt_tokens, completion_tokens, total_tokens}`, from the real
  provider's own reported usage (or the mock's word-count estimate — see §3.1/§ mock below).
- `cost: float | None` — computed per-provider (§3.1); `0.0` for every non-Anthropic real provider
  by design, and a placeholder rate for mock.
- `context_token_budget` / `output_token_budget` — the node's budgets *at the time this run
  executed* (a snapshot, since node config can change later).
- `estimated_context_tokens` — pre-call heuristic estimate (§4), computed before the model is
  ever invoked.
- `token_budget_report: dict` — full per-block breakdown (`TokenBudgetResult.to_report_dict()`,
  `token_budget.py:122-142`): every block's priority, label, estimated tokens, and
  included/truncated/dropped status.

For a DRAFT run, `LoopEngineService.run_loop()` **sums** `prompt_tokens`/`completion_tokens`/`cost`
across every loop iteration's `generate()` call (`loop_engine.py:180-218`) — so a 3-iteration loop
reports the total cost of all 3 model calls, not just the final draft's. `token_budget_report` on
the `AgentRun`, however, is only the **last** iteration's breakdown (explicitly noted as "the most
relevant one — earlier iterations' context has already been superseded",
`loop_engine.py:90-97`).

There is **no hard budget-limit enforcement** anywhere in this pipeline. `TokenBudgetService`
compresses/truncates/drops lower-priority blocks to *fit* the configured budget
(`token_budget.py:150-198`) — it never raises, blocks the call, or refuses to proceed if content
doesn't fit; `TokenBudgetResult.over_budget` is an informational flag only. No cost-ceiling /
spend-limit check was found anywhere in `ai_generation.py`, `loop_engine.py`, or the agent-run
route. **"Budget limit" in this codebase currently means "context window budget," not "spend
budget."** This is relevant to the "budget limit" golden-dataset scenario (§ below) — see
`docs/architecture/fixtures/golden-eval/09-budget-limit.json`, which documents this exact gap
rather than fabricating a spend-limit mechanism that doesn't exist.

Implementation/Testing/PR-Review runs record the same three-key `token_usage`/`cost` shape on
their own respective models (`ImplementationRun`, `TestRun`, `PRReviewRun`), populated directly
from the single `generate_raw_text()` call each makes (`implementation_runs.py:325-328`,
`test_runs.py:208-211`, `pr_review_runs.py:224-227`) — no loop, so no summing needed.

---

## 6. Current clarification and validation loops

### 6.1 Clarification

Model-side contract, not a separate service: every real-provider call is instructed to respond
with a fixed two-part format — a one-line JSON header
(`{"needs_clarification": bool, "clarification_questions": [...]}`) then `---` then the actual
content (`ai_generation.py:35-43,399-421`). `_parse_response()` (`ai_generation.py:424-437`) parses
this; on a malformed header it logs a warning and treats the *entire* response as content rather
than crashing the run — a deliberate resilience choice, not a bug.

A clarification result short-circuits everything downstream: `generate()` returns it directly
(no validation), and inside the loop engine, `LoopEngineService.run_loop()` returns immediately at
whichever iteration produced it, setting `run.loop_status = WAITING_FOR_INPUT`
(`loop_engine.py:231-255`). The route then calls `graph_engine.mark_waiting_for_input(node)`
(`agent_runs.py:357-358`) — the workflow node itself reflects "needs human input" state, visible
in the graph.

### 6.2 Validation + repair loop (DRAFT actions only)

`LoopEngineService.run_loop()` (`loop_engine.py:130-325`) drives:
`PLAN → RETRIEVE_CONTEXT → GENERATE_DRAFT → VALIDATE → [IMPROVE → VALIDATE]* → READY_FOR_REVIEW`.

Stop conditions, checked in this exact order after every VALIDATE step
(`loop_engine.py:275-293`):

1. `quality_score >= quality_threshold` → `COMPLETED_QUALITY_MET`
2. `iteration >= max_iterations` → `COMPLETED_MAX_ITERATIONS`
3. `not validation.has_critical_issues` (below threshold but nothing critical) →
   `COMPLETED_NO_CRITICAL_ISSUES`
4. otherwise → run `IMPROVE` with `validation.suggestions` (falling back to `critical_issues` if
   suggestions is empty) threaded in as `validation_feedback`, iterate again.

Defaults: `DEFAULT_MAX_ITERATIONS = 3`, `DEFAULT_QUALITY_THRESHOLD = 0.8`
(`loop_engine.py:67-68`) — overridable per stage via `ValidatorDefinition.quality_threshold`
(`agent_runs.py:295`); `max_iterations` itself has no per-stage override wired into the route
(always `DEFAULT_MAX_ITERATIONS` in the current call site — confirmed no `max_iterations=` kwarg
passed at `agent_runs.py:282-296`).

Validation is performed by `app/services/validator_agent.py:run_validator()` — an **independent**
second agent, distinct from `AgentPromptRole.VALIDATE` (a human-triggered top-level action where
the *drafting* agent checks its own work). Same real-AI/heuristic-fallback contract as §1.4. The
heuristic fallback (`_run_heuristic_validator`, `validator_agent.py:136-202`) is a real, non-trivial
scoring function — word-count thresholds, keyword-based criteria coverage, structural clarity
proxy, risk-keyword coverage — not a stub; every score and `critical_issues`/`suggestions` entry
it produces is legible without a paid provider.

Every PLAN / RETRIEVE_CONTEXT / GENERATE_DRAFT / VALIDATE / IMPROVE / READY_FOR_REVIEW step of
every iteration is persisted as one immutable `AgentRunLoopEvent` row
(`apps/api/app/models/agent.py:198-232`), surfaced via `GET /agent-runs/{id}/loop-events`
(`agent_runs.py:392`).

### 6.3 Validation and repair outside the loop (Implementation/Testing/PR-Review)

None of the three structured JSON agents has an automatic repair loop — each produces one
result per invocation. "Repair" for those three is entirely human-mediated: a human
accepts/rejects an `ImplementationRun`'s diff (`POST /implementation-runs/{id}/review`), and a
rejected/changed-requested artifact is re-run manually, not auto-retried.

---

## 7. Current implementation proposal workflow

Gate (enforced in the route, before the agent is ever called,
`implementation_runs.py:129-262`):

1. For a story-scoped task: the story's `LLD_REVIEW` lane node must be `COMPLETED`, and the
   `IMPLEMENTATION` lane node must not be `LOCKED`.
2. For a project-level task: `GraphEngineService.validate_can_run()` against the `implementation`
   `WorkflowNode` — enforces both LLD-approved and Implementation-Plan-approved via that node's
   `required_inputs`.
3. `task.area` must be one of `SUPPORTED_AREAS = {BACKEND, FRONTEND, DATABASE, DOCS}`
   (`implementation_agent.py:42-47`) — Testing/Infra areas are rejected with 400 before any agent
   call.
4. A GitHub `Repository` + at least one `RepositorySnapshot` must exist for the project.

Output: `ImplementationAgentResult` — `proposed_file_changes` (path, change_type, summary,
**full proposed after-content**, not a diff-only representation), `diff_text` (a stdlib-`difflib`
unified diff, for human reading only — the actual GitHub write uses `after_content`, not the diff
text, since no patch-application library exists in this codebase —
`implementation_agent.py:62-71`), `explanation`, `test_command`, `risks`, `pr_description`.

**Hard rule, enforced structurally, not just documented:** generating and reviewing a run never
writes to GitHub. The only write path is `POST /implementation-runs/{id}/create-pull-request`,
reachable only once `run.status == COMPLETED and run.review_status == ACCEPTED`
(`implementation_runs.py:438-443`), and even then it always creates a **new** feature branch
(`agent/{slug}-{run-id-prefix}`) and refuses to target the repository's own default branch as that
branch name (`implementation_runs.py:462-467`) — never a direct write to `main`/default.

Human review step: `POST /implementation-runs/{id}/review` records `ACCEPTED`/`REJECTED` with an
optional comment — this is a plain field write plus audit log, not a workflow-node state
transition or a `Review`/`Artifact` row (this feature is fully bespoke, no `Artifact`/`Review`
rows are created for implementation runs — confirmed absent from `implementation_runs.py`
entirely).

---

## 8. Current testing behavior

**No test-execution sandbox exists anywhere in this codebase.** This is stated explicitly and
repeatedly in the source (`testing_agent.py:12-17` and again in the rendered report body,
`testing_agent.py:265-269`): `tests_executed` and the derived pass/fail counts are the agent's own
**reasoning-based assessment** of the diff against acceptance criteria — never a real CI run.

Gate (`test_runs.py:126-144`): latest `ImplementationRun` for the task must be
`review_status == ACCEPTED`, and either a `PullRequestLink` exists or the run's `diff_text` is
non-empty.

Heuristic fallback (`_build_heuristic_result`, `testing_agent.py:101-132`) is explicitly honest by
construction: `tests_executed=[]` always (never fabricates a pass), and it only ever flags a
concrete, checkable fact — the literal string `"TODO(implementation-agent)"` appearing in the diff
(the heuristic implementation agent's own scaffold marker) — as a bug, rather than inventing
findings.

Output persists as `TestRun` fields (`pass_count`, `fail_count`, `bugs_found`,
`suggested_fixes`, `coverage_impact` — the last one is **always** the fixed
`COVERAGE_IMPACT_PLACEHOLDER` dict, `"estimated_change": "unknown"` — no coverage tool is wired up
anywhere, `testing_agent.py:51-54`) **and** a real `test_report` Artifact
(`ArtifactStatus.READY_FOR_REVIEW`) plus a `Review` row through the existing generic review
mechanism (`test_runs.py:215-262`). QA approval happens through that existing `Review` flow —
nothing in `testing_agent.py` or the `test_runs.py` route itself ever sets
`ArtifactStatus.APPROVED`.

---

## 9. Current PR review behavior

Gate (`pr_review_runs.py:120-139`, stricter than Testing's either/or): the task's latest
`ImplementationRun` must be `ACCEPTED`, **and** a `PullRequestLink` must exist for it, **and**
`diff_text` must be non-empty.

Best-effort context (never blocks a run on failure): live PR title/body via
`github_api.get_pull_request()`, approved `lld_document` summary, related `Story` (parsed from the
approved `story_backlog` via `find_related_story`), coding-standards/architecture-rule RAG chunks,
and the latest completed `TestRun`'s pass/fail summary.

Output: `overall_recommendation` (`APPROVE | REQUEST_CHANGES | COMMENT_ONLY`),
`critical_findings`/`major_findings`/`minor_findings`, `missing_tests`, `suggested_comments`,
`risk_score` (0-100), `final_reviewer_note` (a fixed disclosure string: "This is an AI-generated
review to assist, not replace, human judgment... this PR has not been merged").

**Honesty rule, enforced in code, not just prose:** the heuristic fallback path
**never returns `APPROVE`** — only `REQUEST_CHANGES` (if a critical finding exists) or
`COMMENT_ONLY` (`pr_review_agent.py:106-110`). The real-AI path validates
`overall_recommendation` against the three allowed values and coerces anything invalid to
`COMMENT_ONLY` — **never silently defaults an unparseable value to `APPROVE`**
(`pr_review_agent.py:212-216`). No merge call exists anywhere in `github_integration.py` — "must
not merge" is enforced by the absence of that capability, not a runtime check
(`pr_review_runs.py:11-13`).

Posting comments (`POST /pr-review-runs/{id}/post-comments`) writes real GitHub issue comments for
a human-selected/edited subset of `suggested_comments` — the only write this feature performs, and
never anything beyond a plain comment.

### 9.1 "Stale PR review" — confirmed gap, not a documented feature

**No staleness detection exists.** Grepped the full backend for `stale`/`Stale` — no hits related
to PR review at all (only unrelated docstring mentions of "stale cache," "stale client preview,"
and "stale repo snapshot" elsewhere in the codebase). Nothing in `pr_review_agent.py` or
`pr_review_runs.py` checks whether new commits landed on the PR (or on the underlying
`ImplementationRun`/diff) since a previous `PRReviewRun` completed. Starting a second
`PRReviewRun` for the same task simply runs a fresh review against whatever `diff_text` the
*latest accepted* `ImplementationRun` currently holds — there is no comparison against, or warning
about, a prior `PRReviewRun`'s own review having gone stale. This is documented as a genuine gap
in §12 and reflected honestly (not fabricated) in the golden dataset's stale-PR-review fixture.

---

## 10. Existing security and authorization boundaries

- **No authentication/session layer.** Every mutating endpoint trusts an explicit actor id in the
  request body, resolved to a `User` row and checked by `role`
  (`apps/api/app/services/permissions.py:1-17`). This is a real 403-enforcing authorization layer
  over that trusted actor id, not merely an audit label — but the actor id itself is
  self-asserted by the caller today.
- **Role-based stage gates**: `STAGE_EDIT_ROLES` / `STAGE_APPROVE_ROLES`
  (`permissions.py:28-69`) — per-workflow-stage sets of allowed `UserRole` values.
  `require_can_edit_stage()` covers both "create/edit an artifact" and "start an agent run against
  this stage." `ADMIN` always passes every check; `VIEWER` is denied everywhere by construction
  (never appears in any allowed set). Five of the approve-role rows are explicit product
  requirements; every edit-role row and several approve-role rows are documented inline as
  **inferred defaults**, not yet specified by product — a real gap to close, not a bug, but worth
  flagging for Phase 01+ if authorization hardening is in scope.
- **Secrets at rest**: GitHub PATs are encrypted with Fernet (AES-128-CBC + HMAC) via
  `app/core/security.py`, keyed by `GITHUB_TOKEN_ENCRYPTION_KEY`. `decrypt_repository_token()`
  decrypts "exactly once, for exactly one outbound GitHub call — never cached, never logged"
  (`github_integration.py` route, `decrypt_repository_token` docstring). Provider API keys
  (Anthropic/Gemini/OpenRouter/NVIDIA) are plain environment variables (`Settings`), not encrypted
  at rest — standard for server-side process config, but worth naming explicitly since this phase
  is about a "universal agent runtime" that may eventually run keys per-tenant.
- **Audit log**: `record_audit_log()` (`app/services/audit.py`) — every state-changing agent/PR/
  implementation/review action writes an `AuditLog` row (`action`, `entity_type`, `entity_id`,
  `actor_user_id`, `extra_data`). Confirmed discipline: `extra_data` for token/PR-related actions
  explicitly excludes secrets (tokens, full comment bodies in some cases) — e.g.
  `implementation_runs.py:524-532` comments "SECURITY: never the token — only non-secret PR
  metadata."
- **No chain-of-thought exposure found**: none of the inspected agent services (`ai_generation.py`,
  `validator_agent.py`, `implementation_agent.py`, `testing_agent.py`, `pr_review_agent.py`)
  request or surface a reasoning/thinking trace to any API response or persisted field — Anthropic
  calls do set `thinking={"type": "adaptive"}` (`ai_generation.py:459`), but only `response.content`
  blocks of `type == "text"` are ever read back (`ai_generation.py:466`); any thinking-block content
  Anthropic returns is not consumed or stored.
- **GitHub write scope discipline**: no push to a repository's default branch is possible by
  construction (every PR-creating call requires a freshly named feature branch; see §7). No merge
  capability exists anywhere in `github_integration.py` (confirmed by symbol search — only
  `create_pull_request`, never a merge endpoint).

---

## 11. APIs and database behavior that must be preserved

### 11.1 API surface touched by this phase's subject matter (method, path, purpose)

| Method | Path | Purpose |
|---|---|---|
| POST | `/agent-runs` | Start a DRAFT/IMPROVE/VALIDATE run against a workflow node (§1.1-1.2) |
| GET | `/agent-runs/{id}` | Fetch one run |
| GET | `/agent-runs/{id}/loop-events` | Full loop step history (§6.2) |
| POST | `/agent-runs/{id}/save-to-artifact` | Persist a completed run's output as an artifact draft |
| POST | `/implementation-runs` | Start an Implementation Agent run (§7) |
| GET | `/implementation-runs/{id}` | Fetch one run |
| POST | `/implementation-runs/{id}/review` | Human accept/reject decision |
| POST | `/implementation-runs/{id}/create-pull-request` | The only GitHub write in this feature |
| POST | `/test-runs` | Start a Testing Agent run (§8) |
| GET | `/test-runs/{id}` | Fetch one run |
| POST | `/pr-review-runs` | Start a PR Review Agent run (§9) |
| GET | `/pr-review-runs/{id}` | Fetch one run |
| POST | `/pr-review-runs/{id}/post-comments` | Post a human-selected subset of suggested comments to GitHub |

These response shapes (`AgentRunRead`, `ImplementationRunRead`, `TestRunRead`, `PRReviewRunRead`
and their nested schemas in `apps/api/app/schemas/`) are the **existing API contract**. Any Phase
01+ change must extend these additively (new optional fields) rather than rename/remove existing
fields, per the mandatory preservation rules.

### 11.2 Database objects that must be preserved

- `agent_definitions`, `agent_prompts`, `agent_runs`, `agent_run_loop_events` (§2-6)
- `workflow_nodes` / `workflow_edges` (project-level graph engine, §GraphEngineService)
- `story_delivery_lanes`, `story_delivery_nodes`, `story_delivery_edges` (§ Story Delivery Lanes)
- `implementation_runs`, `test_runs`, `pr_review_runs`, `pull_request_links`
- `knowledge_chunks` (with its `vector(384)` pgvector column — `EMBEDDING_DIM = 384`,
  `embeddings.py:28`), `knowledge_sources`
- `repositories`, `repository_snapshots`, `repository_file_index`, `integration_connections`
- `audit_logs`

`GraphEngineService` is confirmed (by source inspection, §GraphEngineService below) to remain the
single authoritative place that reads/writes `WorkflowNode.status`, `blocked_reason`, and
`override_reason` — its own module docstring states this as a hard rule
(`graph_engine.py:1-10`), and no other code path setting these fields directly was found during
this inspection.

### GraphEngineService — authoritative rules (confirmed)

1. `validate_can_run` — a node may run only from `RUNNABLE_STATUSES` (`READY`,
   `NEEDS_CHANGES`, `WAITING_FOR_INPUT`, `FAILED`) **and** every required upstream artifact is
   `APPROVED` (or supplied as freeform input) — `graph_engine.py:48-174`.
2. `unlock_next_nodes` — a downstream node unlocks to `READY` only once *every* non-rework
   incoming edge's source is `APPROVED | COMPLETED | SKIPPED` — supports fan-out/fan-in
   (`graph_engine.py:278-313`).
3. `mark_blocked` — the only path to `BLOCKED`, always with a reason, always audited
   (`graph_engine.py:216-234`).
4. `manual_override` — the only path that bypasses rules 1-2; always requires a reason, always
   audited, `ADMIN`-only via `permissions.py` (`graph_engine.py:238-274`).
5. `validate_evidence_requirement` — an optional per-stage structural gate (a named section must
   exist and be non-empty) checked before an approval can succeed (`graph_engine.py:317-342`).

### Story Delivery Lane / Node (confirmed model shape)

- `StoryDeliveryLane`: one per `story_id` (unique constraint), `status` (`ACTIVE` default),
  `current_node_id`, owns an ordered list of `StoryDeliveryNode` + `StoryDeliveryEdge` rows.
  Deliberately **not** built on the project-level `WorkflowNode`/`WorkflowEdge` graph — a
  separate, purpose-built model (`story_delivery_lane.py:12-28`).
- `StoryDeliveryNode`: sequential by `order_index`; default node sequence (10 stages):
  `STORY_READY → STORY_LLD → LLD_REVIEW → IMPLEMENTATION → PULL_REQUEST → PR_REVIEW_AGENT →
  HUMAN_CODE_REVIEW → TESTING → QA_APPROVAL → RELEASE_READY` (`story_delivery_node.py:16-27`).
  Every node except the first starts `LOCKED`, unlocked to `READY` only once its immediate
  predecessor reaches `COMPLETED` (`story_delivery_node.py:30-37`).
- `assigned_role`/`assigned_user_id` are advisory only — **no per-lane-node permission table
  exists**; the project-level `permissions.py` role gates (§10) are what's actually enforced for
  story-lane actions today (`story_delivery_node.py:49-54`).

### AgentDefinition / AgentPrompt / AgentRun / AgentRunLoopEvent (confirmed field shapes)

See full field lists in §2-6 above; the authoritative source is
`apps/api/app/models/agent.py:13-232`.

### "Vertical" / "Horizontal" story generation (confirmed terminology)

The phrase used in the prompt for this document ("vertical story generation," "horizontal story
generation") does **not** appear verbatim in the runtime services under direct review, but a
matching, already-implemented concept **does** exist and was located during test-suite execution:

- `StoryType` enum (`apps/api/app/models/enums.py:364-372`): `VERTICAL` = "end-to-end user-value
  stories (the existing default shape)"; `HORIZONTAL` = "technical-layer stories
  (frontend/backend/database/integration/infrastructure/testing/documentation)."
- Produced by the `story_crafting` agent's prompt (`RICH_DEFAULT_PROMPTS["story_crafting"]` in
  `app/db/seed.py`, not opened in full during this pass) via a `**Mode:** VERTICAL|HORIZONTAL`
  field per story block, parsed by `app/services/story_export.py:parse_story_backlog()`, and
  persisted onto the real `Story` row by `sync_stories_from_backlog`
  (`apps/api/app/api/routes/stories.py`) — covered by
  `apps/api/tests/test_story_crafting_vertical_horizontal.py` (6 tests, all passing).
- Separately, `story_lld_agent.py`'s own docstring distinguishes "the project-level `lld` stage,
  which designs for a whole story backlog at once" from "a Story LLD... scoped to exactly one
  story" (`story_lld_agent.py:1-6`) — a second, adjacent "wide vs. deep" distinction worth not
  conflating with `StoryType.VERTICAL/HORIZONTAL` above; they are two different axes (story
  *shape* vs. LLD *scope*), both real, neither fabricated for this document.

---

## 12. Recommended feature-flag migration points

Per the mandatory strangler-migration rule (keep the current implementation behind a flag until
its replacement passes evaluation), the following are the concrete seams identified in this pass
where a Phase 01+ "universal agent runtime" replacement could be gated:

1. **Provider resolution** (`get_active_provider`) — a single function with one call site pattern
   (`if provider == ...: elif ...`) repeated in `ai_generation.generate`, `generate_raw_text`.
   Any new provider-routing logic (e.g. a real per-agent `model_name`-driven selection, replacing
   the currently-unused `AgentDefinition.model_name` field) can gate here behind a flag without
   touching call sites.
2. **Context assembly** — `build_prioritized_context` is the one live implementation; migrating
   toward `ContextBuilderService`'s already-built structured pipeline (§1.6, §4) — or a new
   implementation — is a natural flagged swap, since the dead code already establishes the target
   interface shape and both already share `ContextBlock`/`TokenBudgetService`.
3. **Token budgeting** — `TokenBudgetService`'s 4-chars/token heuristic (§4) is an isolated,
   narrow-interface component (`estimate_tokens`, `ContextBlock`, `.build()`); swapping in a real
   tokenizer per provider is flaggable at this single seam without touching callers, as long as
   the `TokenBudgetResult` shape is preserved.
4. **Loop engine stop conditions / max_iterations override** — `DEFAULT_MAX_ITERATIONS` has no
   per-stage override wired in today (§6.2); introducing one (e.g. from `ValidatorDefinition` or a
   new `WorkflowNode` field) is additive and flaggable without changing the loop's control flow.
5. **Validator real-AI path** — already isolated behind `get_active_provider() == "mock"` in
   `validator_agent.py`; any new validator strategy can sit behind the same seam.
6. **Structured JSON agents' schema** — `implementation_agent.py`/`testing_agent.py`/
   `pr_review_agent.py` each hardcode their own JSON-shape system prompt and parsing. A shared
   "universal structured agent" abstraction (common JSON-schema validation, common retry contract)
   could be introduced behind a flag per agent, since all three already share an identical
   resilience contract (§1.4) that could be centralized.
7. **"Stale PR review" detection** (§9.1) — currently absent entirely, not merely flag-gated. Any
   new implementation is pure addition (a new check before `start_pr_review_run` proceeds, or a
   new field comparing PR HEAD SHA/commit count against the SHA a completed `PRReviewRun` last
   evaluated) — flaggable from day one since there is no existing behavior to preserve here.
8. **Spend-limit enforcement** (§5) — also currently absent entirely (only context-window budget
   exists). A new cost-ceiling check is pure addition and should default OFF behind a flag until
   evaluated, since introducing a hard stop where none existed is itself a behavior change worth
   gating carefully (a false-positive block would halt a previously-working flow).

---

## 13. Test suite baseline

Command run: `apps/api/.venv/Scripts/python.exe -m pytest -q`, from `apps/api/`.

**Pre-existing environment issue found and worked around (not a code change):** the local,
git-ignored `apps/api/.env` contained two stray keys (`HUGGINGFACE_API_KEY`, `HUGGINGFACE_MODEL`)
not declared on the Pydantic `Settings` model, which caused `Settings()` construction to raise
`ValidationError` (`extra_forbidden`) and made 21 test files fail to even *collect*. This is a
local developer-environment artifact, not application code — nothing in `app/core/config.py` or
any production file was modified. To obtain a real baseline, the two stray lines were temporarily
removed from the git-ignored `.env` for the duration of the single test run, then the file was
restored byte-for-byte from a backup immediately afterward (verified via `diff` showing zero
difference before/after). **Recommendation for Phase 01+:** either add
`huggingface_api_key`/`huggingface_model` as recognized (optional) `Settings` fields, or set
`model_config = SettingsConfigDict(extra="ignore")` on `Settings`, so a stray local `.env` entry
can never again block test collection for every contributor on this machine.

**Result: 406 passed, 0 failed, 22 warnings, in 30.98s.**

Warnings are all pre-existing and unrelated to this phase's subject matter: a
`PendingDeprecationWarning` about `python_multipart`, a `PydanticDeprecatedSince20` warning about
class-based `Config`, several `PytestCollectionWarning`s from pytest mistaking model/schema
classes literally named `TestRun`/`TestRunStatus`/`TestAgentType`/etc. for test classes (a naming
collision with pytest's own `Test*` collection convention — cosmetic only), and one
`PytestReturnNotNoneWarning` from `test_ollama.py::test_ollama_connection` returning `False`
instead of asserting (pre-existing, unrelated to this phase).

Relevant test files confirmed present and passing (subset, by subject matter of this phase):
`test_ai_generation_providers.py`, `test_token_budget.py`, `test_loop_engine.py`,
`test_context_builder.py`, `test_context_builder_full_content_rules.py`,
`test_repo_context_builder.py`, `test_retrieval.py`, `test_graph_engine.py`,
`test_implementation_agent.py`, `test_implementation_runs.py`, `test_implementation_planner.py`,
`test_testing_agent.py`, `test_test_runs.py`, `test_pr_review_runs.py`,
`test_pull_request_creation.py`, `test_story_lld.py`, `test_story_delivery_lane.py`,
`test_story_implementation.py`, `test_story_crafting_vertical_horizontal.py`,
`test_lld_agent_prompt.py`, `test_lld_workflow.py`, `test_sprint_planning.py`,
`test_maintenance_runs.py`, `test_infrastructure_planning_stage.py`,
`test_section_improve_agent.py`, `test_github_integration.py`, `test_jira_routes.py`,
`test_confluence_routes.py`, `test_stories_routes.py`.

**Frontend test suite: UNKNOWN.** Not run in this pass — this phase's mandate was backend-focused
agent runtime inspection; no `apps/web` test command was invoked. If a frontend test run is
required for a complete Phase 00 baseline, it should be executed and appended here rather than
assumed to pass.

**Metrics not measured in this pass (marked `UNKNOWN`, not fabricated):**
- Real-provider latency per call, per provider.
- Real-provider token counts vs. the 4-chars/token estimate's actual drift, in production traffic.
- Real spend accrued to date (no spend-tracking mechanism exists to query — see §5).
- Frontend build/lint/test status.
- Production error rates for `AIGenerationError` by provider.

---

## 14. Existing agent-run / implementation / testing / PR-review UI (confirmed touchpoints)

Frontend: `apps/web/` (Next.js). Confirmed relevant surfaces by file inspection:

- `apps/web/app/agent-runs/` — agent run pages.
- `apps/web/components/documents/agent-actions-panel.tsx`,
  `apps/web/components/documents/clarification-panel.tsx` — the clarification-required UI state
  (§6.1) has a dedicated component, confirming the frontend does branch on
  `needs_clarification`/`loop_status` rather than treating every completed run identically.
- `apps/web/components/implementation-plan/implementation-run-panel.tsx` — "Run Implementation
  Agent" + review UI; its own header comment states the same boundary as the backend: "This only
  ever produces a proposed diff for a human to read; nothing here — including Accept — ever writes
  to the repository or GitHub" (lines 22-26), and calls `api.implementationRuns.start(...)` from
  `apps/web/lib/api.ts`, confirming the frontend uses the exact `/implementation-runs` contract
  documented in §11.1, not a shadow/alternate API.
- `apps/web/components/testing/test-run-panel.tsx` — Testing Agent run UI.
- `apps/web/components/pr-review/pr-review-panel.tsx`, `pr-review-view.tsx` — PR Review Agent run
  UI, under `apps/web/app/projects/[projectId]/pr-review/`.
- `apps/web/lib/api.ts` — confirmed `implementationRuns`, `testRuns`, `prReviewRuns`, and
  `agentRuns` client namespaces exist and map to the backend routes in §11.1 (line numbers 1199,
  1283, 1297, 1316, 1447 at time of inspection).

Deeper UI-state inspection (loading/error/per-field rendering for every panel) was not performed
line-by-line in this pass — the above confirms the UI is wired to the documented API contract and
branches on the documented clarification/loop state, which was the material fact needed for this
baseline.

---

## Appendix: Files inspected directly (file:line citations used throughout this document)

- `apps/api/app/services/ai_generation.py`
- `apps/api/app/services/token_budget.py`
- `apps/api/app/services/loop_engine.py`
- `apps/api/app/services/context_builder.py`
- `apps/api/app/services/retrieval.py`
- `apps/api/app/services/embeddings.py`
- `apps/api/app/services/graph_engine.py`
- `apps/api/app/services/validator_agent.py`
- `apps/api/app/services/implementation_agent.py`
- `apps/api/app/services/testing_agent.py`
- `apps/api/app/services/pr_review_agent.py`
- `apps/api/app/services/mock_agent.py`
- `apps/api/app/services/permissions.py`
- `apps/api/app/services/audit.py`
- `apps/api/app/core/config.py`
- `apps/api/app/core/security.py`
- `apps/api/app/models/agent.py`
- `apps/api/app/models/story_delivery_lane.py`
- `apps/api/app/models/story_delivery_node.py`
- `apps/api/app/models/enums.py`
- `apps/api/app/api/routes/agent_runs.py`
- `apps/api/app/api/routes/implementation_runs.py`
- `apps/api/app/api/routes/test_runs.py`
- `apps/api/app/api/routes/pr_review_runs.py`
- `apps/api/app/api/routes/github_integration.py`
- `apps/api/app/services/github_integration.py`
- `apps/api/app/services/jira_integration.py`
- `apps/api/tests/test_story_crafting_vertical_horizontal.py`
- `apps/web/components/implementation-plan/implementation-run-panel.tsx`
- `apps/web/lib/api.ts`

Files referenced by name but not opened line-by-line in this pass (flagged, not silently assumed):
`app/db/seed.py` (RICH_DEFAULT_PROMPTS content), `app/services/artifact_summary.py`,
`app/services/repo_context_builder.py` (usage confirmed, internals not reviewed),
`app/services/confluence_integration.py`, `app/models/workflow.py` (fields referenced via other
files' citations, not opened directly).
