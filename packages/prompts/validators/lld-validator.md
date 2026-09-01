# Low-Level Design (LLD) Validator

| | |
|---|---|
| **Validator key** | `lld-validator` |
| **Workflow stage** | Low-Level Design (`lld`) |
| **Checks drafts of** | `lld_document` (see [../agents/lld-agent.md](../agents/lld-agent.md)) |

## Role

An independent quality check on every drafted or revised LLD document —
it did not write the draft, and its job is to score it honestly, not to
rubber-stamp it. It runs automatically as part of the Loop Engine's
self-improvement cycle after every draft/improve pass (see
`app/services/loop_engine.py`), not on human request.

## Criteria (this stage's rubric)

Configured on this stage's `ValidatorDefinition.criteria` (see
`app/db/seed.py`'s `RICH_DEFAULT_PROMPTS["lld"]["validation_checklist"]`
— kept in sync with [../agents/lld-agent.md](../agents/lld-agent.md)'s own
Quality Checklist):

- All 15 required sections are present, in order, none blank without an explicit "None."
- Uses only the approved Solution Discovery, HLD, and story backlog — nothing outside them.
- No business rule is invented — anything not stated by the input is under Risks and Assumptions.
- Output is developer-ready: concrete enough to implement directly, not just descriptive.
- Every in-scope story maps to at least one API contract or component in this design.
- API Contracts and Request/Response DTOs are concrete enough to implement directly, not just named.
- Database Changes describe the actual schema/migration impact, not just "update the database."
- Permission Rules reference real roles (see `app/services/permissions.py`'s `UserRole`), not invented ones.
- Test Cases cover the Validation and Permission rules stated earlier in the document, not just happy paths.
- Nothing already decided in the HLD or Solution Discovery is silently re-decided.
- Every unresolved decision or dependency is captured under Risks and Assumptions, not silently assumed.

## Output Contract

This validator must return:

- **`qualityScore`** — overall judgment, 0.0–1.0.
- **`completenessScore`** — does the draft cover everything this stage requires, 0.0–1.0.
- **`technicalClarityScore`** — is it well-structured, unambiguous, and precise enough for a
  developer to act on directly, 0.0–1.0.
- **`missingDetails`** — specific required content that's absent or too thin (e.g. a required
  section that's missing, or present but empty).
- **`risks`** — the specific risks or assumptions the draft itself identifies (not just whether
  it identifies any — what they actually are).
- **`recommendation`** — one of `READY_FOR_REVIEW` or `NEEDS_IMPROVEMENT`.

### How this maps onto the platform's shared validator

Every stage's validator shares one runtime contract
(`app/services/validator_agent.py`'s `ValidatorResult`) rather than each
stage defining its own schema — LLD's fields above map onto it directly:

| This contract | `ValidatorResult` field |
|---|---|
| `qualityScore` | `quality_score` |
| `completenessScore` | `completeness_score` |
| `technicalClarityScore` | `clarity_score` |
| `missingDetails` | `missing_details` |
| `risks` | `risks` |
| `recommendation` | `recommendation` — derived: `APPROVE` → `READY_FOR_REVIEW`, `REVISE`/`REJECT` → `NEEDS_IMPROVEMENT` |

`ValidatorResult` also carries `risk_coverage_score` (does the draft call
out risks *at all*, as a score — separate from `risks`, which is *what*
they are), `critical_issues` (blocking problems generally, a superset of
`missing_details`), `suggestions` (concrete improvements), and the
underlying three-value `approval_recommendation` (`APPROVE`/`REVISE`/
`REJECT`) that `recommendation` rolls up from. These aren't part of the
contract requested for LLD specifically, but are already present on every
`ValidatorResult` and available if useful.

## Real vs. Mock

Real AI when a provider is configured (one JSON-structured call per
draft, independent of the drafting agent's own call — see
`get_active_provider`); a deterministic heuristic otherwise, or if the
real call errors or returns unparseable JSON. The heuristic path scores
completeness/clarity/risk-coverage structurally (heading presence, length,
keyword coverage of the criteria above) — it has real judgment for
`missing_details` (the specific criteria it couldn't match) but not for
`risks`, which needs actual reading comprehension the heuristic doesn't
have; that field is empty on the heuristic path.

## Quality Threshold

`ValidatorDefinition.quality_threshold` is this stage's Loop Engine
stop-condition bar — the loop keeps improving a draft until `qualityScore`
meets this threshold, iterations run out, or no critical issues remain to
act on (see `app/services/loop_engine.py`).
