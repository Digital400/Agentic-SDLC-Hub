# Cost-Aware Runtime and Model Router (Phase 17)

**Status:** New, additive backend modules
(`app/services/runtime_cost_router.py`,
`app/services/runtime_cost_metrics.py`,
`app/services/runtime_routing_simulator.py`), one new table
(`runtime_routing_decisions`). Not called from any live route in this
phase — see §5.

## 1. What this phase built

- **`RuntimeCostRouter`** (`runtime_cost_router.py`) — a pure,
  dependency-free decision engine. `route(signals: RoutingSignals) ->
  RoutingDecision` implements Phase 17's exact default policy table:

  | Signal | Behavior |
  |---|---|
  | `previous_failed_repair_attempts >= 2` | Stops for human decision immediately, before evaluating any candidate — "two failed repair attempts: stop for human decision." |
  | `estimated_complexity == "deterministic"` | Selects `("deterministic", "none")` at $0.00 — "no LLM." |
  | Document, `"simple"` | `local-runtime` / small local model — only if a local runtime is actually connected (`user_connection_available`), else rejected with a named reason and the router falls through to stopping for human. |
  | Document, `"normal"`/`"complex"` | `document-runtime` / standard low-cost model. |
  | Coding, `"simple"`/`"normal"` | `opencode` / affordable coding model. |
  | Coding, `"complex"`, no strong-security requirement | `opencode` / standard coding model tried first, `codex` / premium model as a second candidate. |
  | Coding, `"complex"`, `security_requires_strong_model=True` | Escalates directly to `codex` / premium — no standard candidate is even offered. |
  | Every candidate rejected | Stops for human decision, listing every candidate's own `rejected_reason` — never a silent empty result. |

  Rejection reasons are checked per-candidate, independently: runtime
  health, local-runtime connection availability, queue capacity, project
  budget remaining vs. estimated cost, and historical success rate (a
  candidate below a 50% floor is rejected). Every `RoutingDecision`
  records `requested_runtime`/`requested_model`, every `candidates` entry
  (each with its own `rejected_reason` or `None`), the selection,
  `estimated_cost_usd`, `actual_cost_usd` (always `None` at decision
  time — filled in later by a caller once execution completes),
  `escalation_reason`, and `cost_owner` — exactly the fields Phase 17
  asks to be recorded.

- **`RuntimeRoutingDecision`** (new table, migration `1113263641ba`) —
  persists one row per `route()` call, plus four downstream outcome
  flags (`artifact_approved`, `patch_accepted`, `story_merged`,
  `execution_failed`) that start `False` and are only ever set later by
  whatever real event confirms them.

- **`runtime_cost_metrics.py`** — the six measurements Phase 17 names:
  `cost_per_approved_artifact`, `cost_per_accepted_patch`,
  `cost_per_merged_story` (each: average `actual_cost_usd` across
  decisions with that outcome flag set, or `None` — not `0.0` — when
  there is no data yet, since "no data" and "zero cost" are different
  facts), `premium_runtime_percentage` (excludes stopped-for-human
  decisions from the denominator), `cache_savings` (a running sum,
  `0.0` is a real value here), and `failed_attempt_cost` (sum of actual
  cost spent on decisions later flagged `execution_failed`).

- **`runtime_routing_simulator.py`** — `simulate_routing(scenarios:
  list[RoutingSignals]) -> SimulationSummary`, entirely in-memory (no
  database, no network): runs a batch of scenarios through the router and
  reports how many would stop for a human, how many would select the
  premium candidate, and total/average estimated cost — "add an offline
  routing simulator before enabling Auto in production."

## 2. Why nothing is enabled by default

- **`Settings.RUNTIME_COST_ROUTER_ENABLED`** defaults `False` — nothing
  in this codebase currently calls `RuntimeCostRouter` from a live
  request path. This mirrors Phase 16's own disclosed deferral: a
  cost-routing decision inserted into a live agent-dispatch or
  implementation-run path is exactly the kind of change that deserves a
  dedicated integration pass with its own review, not a same-session
  bolt-on alongside building the router itself.
- **No "Auto is now enabled in production" switch exists anywhere to
  flip** — apps/web's Phase 14 `resolveAutoSelection()` is itself an
  explicitly disclosed placeholder (first available, non-premium option),
  and this phase's own instructions ask for the simulator to exist
  *before* such a switch would ever be built, which is exactly the order
  this phase follows: simulator built, switch not yet built.

## 3. Files changed

- **New:** `app/services/runtime_cost_router.py`,
  `app/services/runtime_cost_metrics.py`,
  `app/services/runtime_routing_simulator.py`,
  `app/models/runtime_routing_decision.py`,
  `alembic/versions/1113263641ba_runtime_routing_decisions.py`,
  `tests/test_runtime_cost_router.py` (19 tests),
  `tests/test_runtime_cost_metrics.py` (10 tests),
  `tests/test_runtime_routing_simulator.py` (6 tests).
- **Modified:** `app/core/config.py` (`RUNTIME_COST_ROUTER_ENABLED`),
  `app/models/__init__.py`, `tests/conftest.py` (new table registration).

## 4. Tests executed and results

```
apps/api> pytest tests/test_runtime_cost_router.py tests/test_runtime_cost_metrics.py tests/test_runtime_routing_simulator.py -q
  -> 35 passed
apps/api> pytest -q (full suite) -> 1096 passed
```

One real bug found and fixed via this test suite: an averaged-cost
metric test failed on binary floating-point rounding
(`0.30000000000000004 == 0.3`) — a real, expected float-arithmetic
artifact of summing `0.2 + 0.4` and dividing by 2, not a router bug;
fixed the test to use `pytest.approx`, not the production averaging
logic.

## 5. Remaining risks / next phase

- **Not wired into any live route.** `RuntimeCostRouter` is complete and
  tested standalone; connecting it to a real dispatch path (Phase 06's
  Celery dispatcher, or a future implementation-run trigger) so
  `RoutingSignals` are built from real project/task state is a deliberate
  follow-up, not done here.
- **No connection to apps/web's Phase 14 UI.** `resolveAutoSelection()`
  there remains its own disclosed placeholder; wiring the frontend's
  "Auto" path to a real backend endpoint backed by this router is a
  separate, cross-stack integration task.
- **The default policy table's cost figures
  (`base_cost` values in `_evaluate`/`_coding_candidates`) are
  illustrative placeholders**, not sourced from any real provider pricing
  table — a real cutover would need these wired to actual
  per-model/per-token pricing (mirroring `ai_generation.py`'s own
  `_MODEL_PRICING_PER_MTOK`), not the flat per-call estimates used here.
