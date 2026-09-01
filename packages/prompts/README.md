# @agentic-sdlc-hub/prompts

Default prompt content for the platform's SDLC agents — versioned,
human-readable, and kept separate from application code so a prompt can be
reviewed and changed without touching a database migration or a deploy.

## Layout

```
agents/
  requirement-intake-agent.md
  problem-discovery-agent.md
  solution-discovery-agent.md
  hld-agent.md
  story-crafting-agent.md
  lld-agent.md
  implementation-planner-agent.md
validators/
  lld-validator.md
```

One file per agent, named after its `agent_key`. Each file is the
authoritative source for that agent's default prompt and follows the same
structure:

- **Agent role** — what the agent is responsible for, and what it's explicitly not.
- **Input required** — the artifact(s) it needs, matching `workflows/sdlc-workflow.json`'s `requiredInputs`.
- **Prompt rules** (where the stage has explicit numbered rules from the product request, e.g. LLD's) — the specific behavioral constraints the agent must follow.
- **Output format** — the exact Markdown structure it must produce.
- **Design rules** — how to handle the input responsibly, beyond the format itself.
- **What not to do** — the mistakes this agent must not make (scope creep into later stages, inventing data, etc.).
- **Quality checklist** — self-check criteria before output is considered ready for review.
- **Human review requirement** — the human-in-the-loop gate this stage sits behind (see docs/product-vision.md).

`validators/` documents the independent quality-check agent for a stage
(see `app/services/validator_agent.py` — a separate concept from the
drafting agent's own prompt above), one file per stage that has one,
named after its `validator_key`. Each documents that stage's specific
criteria/rubric and output contract, and how that contract maps onto the
platform's one shared validator runtime schema (`ValidatorResult`).

## Relationship to the database

Agent prompt content is mirrored into the `agent_prompts` table by
[apps/api/app/db/seed.py](../../apps/api/app/db/seed.py)'s
`RICH_DEFAULT_PROMPTS`, and served through the Prompt Library API
(`apps/api/app/api/routes/prompts.py`) and UI (`apps/web/app/agents/[agentKey]`).
These markdown files are the source of truth for what a *default* prompt
should say; edits made through the Prompt Library itself become new,
separately versioned rows in the database and don't modify these files.

Validator criteria are mirrored the same way, into the
`validator_definitions` table's `criteria` column, from the same
`RICH_DEFAULT_PROMPTS` entry's `validation_checklist` (one rubric shared
by the drafting agent's own self-check and the independent validator).
The validator's *output contract* (the field names/shapes it must return)
is not per-stage configuration — it's the shared, hardcoded schema in
`app/services/validator_agent.py`; a `validators/*.md` file documents how
that stage's desired contract maps onto it.

## Status

No agent engine calls this package yet (see docs/mvp-plan.md — LangGraph
integration is future scope). Today, these files exist to be read by a
human and to keep the seeded default prompts and this documentation in
sync by hand.
