# Structured Promptless Workflow UX (Phase 15)

**Status:** New, additive frontend module
(`apps/web/lib/structured-actions/`, `apps/web/components/structured-actions/`),
wired into the one existing component that rendered open-ended prompt
boxes (`components/documents/agent-actions-panel.tsx`). Opt-in per stage —
a stage with no structured schema keeps its exact existing behavior.

## 1. What existed before this phase

`AgentActionsPanel` rendered one open `<Textarea>` per
`WorkflowNode.required_inputs` entry that had no matching upstream
artifact (`freeformInputKeys`, computed in
`app/documents/[artifactId]/page.tsx`), each submitted under its own raw
key inside `inputContext` — a real, working mechanism, but exactly the
"open-ended prompt text area" pattern this phase asks to reduce.

## 2. What this phase built

- **`lib/structured-actions/schemas.ts`** — `STAGE_FIELD_SCHEMAS`, a typed
  field schema per SDLC stage, keyed by the stage's real
  `WorkflowNode.node_key` (`requirement_intake`, `story_crafting`, `lld`,
  `implementation`, `testing`, `pr_review` — matching this phase's own six
  named forms, and Phase 16's own migration order). Also
  `buildInputContext()` and `missingRequiredFields()`, both pure and unit
  tested independent of any rendering.
- **`components/structured-actions/structured-action-form.tsx`** — the
  `StructuredActionForm` component: renders each field by its declared
  type (`text`/`textarea`/`select`/`multiselect`), enforces required
  fields before allowing a WorkPacket preview or submission, and always
  includes exactly one bounded "Additional context" field
  (`ADDITIONAL_CONTEXT_MAX_LENGTH = 500` characters, enforced via the
  native `maxLength` attribute) — present but explicitly secondary, never
  the primary way to describe the task.
- **A WorkPacket preview** — a "Preview WorkPacket" toggle rendering the
  exact `{ input_context: {...} }` payload that will be sent, before any
  run — "show users a readable Work Packet preview before expensive or
  write-capable runs."
- **Wiring**: `AgentActionsPanel` now calls `getStageSchema(nodeKey)`; if
  a schema exists for the current stage, it renders `StructuredActionForm`
  in place of the raw per-key textarea loop, submitting through the exact
  same `handleRun(inputContext)` / `runAgentAndApply` path as before — a
  new `nodeKey` prop was threaded through
  `ArtifactDocument` → `ArtifactEditor` → `AgentActionsPanel` to make this
  possible (`lib/types.ts`, `lib/mappers.ts`,
  `app/documents/[artifactId]/page.tsx`). No backend endpoint or wire
  contract changed — `input_context` is keyed identically either way.

## 3. Why this design keeps every prior behavior working

- A stage not in `STAGE_FIELD_SCHEMAS` (HLD, Problem Discovery, Solution
  Discovery, Implementation Planning, and any future/custom stage) falls
  through to the *exact* previous rendering branch, unchanged — same
  strangler pattern as every other phase this migration has used.
- `buildInputContext()` produces the same flat `Record<string,string>`
  shape `freeformValues` always sent, so `ai_generation.py`'s
  `build_prioritized_context` (which folds every `input_context` key into
  the model's P0 instructions regardless of key name — confirmed in
  Phase 00's own baseline doc) needs zero changes to consume structured
  field values instead of freeform ones.
- "Preserve existing artifact edit/preview/review behavior" — nothing in
  `artifact-editor.tsx`'s section editing, review, or comment flows was
  touched; only the agent-trigger panel's input collection changed.

## 4. Clarification responses — already satisfies this requirement, no change needed

Phase 15 also asks: "Clarification responses must update structured
fields and regenerate the WorkPacket rather than append unlimited
conversation history." Inspecting the existing
`components/documents/clarification-panel.tsx` (its own module docstring,
verified, not just assumed): it already submits one bounded answer block
under a single `clarification_answers` key per round, not an
ever-growing transcript — "a single consolidated answer works exactly as
well as one key per question, without needing to parse the agent's
bullet list back apart." This already matches the requirement's intent;
no change was made here to avoid touching a working, already-correct
flow without a concrete gap to fix.

## 5. Scope reductions, disclosed

- Only the six stages Phase 15/16 name explicitly have a structured
  schema. Every other stage (HLD, Problem Discovery, Solution Discovery,
  Implementation Planning, and the per-story-lane stages) keeps its
  existing freeform textareas — extending schemas to them is
  straightforward (one new `STAGE_FIELD_SCHEMAS` entry each) but wasn't
  done here since this phase's own field lists didn't specify them.
- Field types are kept to four primitives (`text`/`textarea`/`select`/
  `multiselect`) built from this codebase's existing design system;
  no new autocomplete/typeahead component (e.g. for "Jira project") was
  built — that field is a plain text input for now.

## 6. Tests executed and results

```
apps/web> yarn vitest run          → 4 files, 39 tests, all passing
                                      (16 new this phase: 9 schema logic + 7 component)
apps/web> yarn exec tsc --noEmit   → 0 errors
apps/web> yarn build               → compiles, all 27 routes generate successfully
```

## 7. Remaining risks / next phase

- Structured schemas for the remaining (non-Phase-15-named) stages are a
  straightforward follow-up, not yet done.
- No dedicated component test yet exercises `AgentActionsPanel`'s branch
  selection itself (`getStageSchema(nodeKey)` picking the right form) —
  covered indirectly by `structured-action-form.test.tsx` and the
  successful production build, but not by a panel-level integration test.
