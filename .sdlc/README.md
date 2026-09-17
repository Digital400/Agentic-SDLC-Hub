# `.sdlc/` — canonical prompt, policy, and skill catalog

The source of truth `apps/api/app/prompt_compiler`'s `PromptCompiler` reads
to compile a `RuntimeInstructionPackage` (see `app/agent_runtime`, Phase 01)
for one `WorkPacket`. Nothing in this directory is executed — every file
here is either data (YAML/Markdown) or a generated artifact.

## Layout

```
project.yaml     compiler-wide defaults (default policy set, token ceilings)
policies/
  <key>/vN.yaml  one versioned company/project policy
skills/
  <key>/vN.md    one versioned skill — see below
schemas/          JSON Schema for skill.yaml/policy.yaml/project.yaml's own
                  shape, GENERATED from the Pydantic models that load them
                  (apps/api/app/prompt_compiler/schema_export.py)
generated/         optional runtime-specific instruction files, GENERATED
                  from the skills/ + policies/ catalog above
                  (apps/api/app/prompt_compiler/vendor_export.py) — never
                  hand-edited; regenerate after any skill/policy change.
```

## Skill file format

`skills/<key>/v<N>.md` — a YAML frontmatter block (name, version, purpose,
applicable_task_types, required_inputs, allowed_tool_categories,
prohibited_actions) followed by Markdown `##` sections (Procedure,
Validation Checklist, Output Contract, Examples). One directory per skill,
one file per version — a skill's version is never edited in place; a
change adds a new `vN.md` beside the old one.

Every skill's `applicable_task_types` maps onto exactly one
`app.agent_runtime.WorkPacketTaskType` value — the catalog is required to
cover all 13 task types exactly once (see
`tests/test_prompt_compiler_catalog.py::test_every_work_packet_task_type_has_exactly_one_skill`).

## Relationship to `packages/prompts/` and the `AgentPrompt` database table

**This is a separate, additive catalog — nothing here replaces or
modifies either existing system, and neither of them is touched by this
directory's introduction.**

- `packages/prompts/agents/*.md` remains exactly what it always was: the
  human-readable source `apps/api/app/db/seed.py`'s `RICH_DEFAULT_PROMPTS`
  mirrors into the `agent_prompts` table, keyed by `agent_key` — the prompt
  content every real agent run in this application actually uses today
  (see `docs/architecture/universal-agent-runtime-baseline.md`).
- `skills/` here is keyed by `WorkPacketTaskType` instead, versioned as
  real files (not database rows), and read by `PromptCompiler` — a new,
  parallel compilation path that **no route or service calls yet**. See
  `apps/api/app/prompt_compiler/compiler.py`'s module docstring for this
  phase's explicit "additive, not connected" status.

If/when a future phase migrates the live generation path onto
`PromptCompiler`, that migration is expected to happen behind a feature
flag, the same strangler pattern `ProjectExecutionProfile` (Phase 03) and
`app/agent_runtime` (Phase 01) already established in this codebase — not
by deleting `packages/prompts/` or the `agent_prompts` table.

## Regenerating generated content

```
cd apps/api
.venv/Scripts/python.exe -m app.prompt_compiler.schema_export
.venv/Scripts/python.exe -m app.prompt_compiler.vendor_export
```

`tests/test_prompt_compiler_generation.py` fails if either output drifts
from what's committed — regenerate and commit the diff, the same
discipline `app/agent_runtime/schema_export.py` (Phase 01) already
established for its own generated JSON Schemas.
