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
```

One file per agent, named after its `agent_key`. Each file is the
authoritative source for that agent's default prompt and follows the same
structure:

- **Agent role** — what the agent is responsible for, and what it's explicitly not.
- **Input required** — the artifact(s) it needs, matching `workflows/sdlc-workflow.json`'s `requiredInputs`.
- **Output format** — the exact Markdown structure it must produce.
- **Rules** — how to handle the input responsibly.
- **What not to do** — the mistakes this agent must not make (scope creep into later stages, inventing data, etc.).
- **Quality checklist** — self-check criteria before output is considered ready for review.
- **Human review requirement** — the human-in-the-loop gate this stage sits behind (see docs/product-vision.md).

## Relationship to the database

This content is mirrored into the `agent_prompts` table by
[apps/api/app/db/seed.py](../../apps/api/app/db/seed.py)'s
`RICH_DEFAULT_PROMPTS`, and served through the Prompt Library API
(`apps/api/app/api/routes/prompts.py`) and UI (`apps/web/app/agents/[agentKey]`).
These markdown files are the source of truth for what a *default* prompt
should say; edits made through the Prompt Library itself become new,
separately versioned rows in the database and don't modify these files.

## Status

No agent engine calls this package yet (see docs/mvp-plan.md — LangGraph
integration is future scope). Today, these files exist to be read by a
human and to keep the seeded default prompts and this documentation in
sync by hand.
