# Architecture — Agentic SDLC Hub

Status: foundation stage. This document reflects the intended shape; most of
it is not implemented yet and will be filled in incrementally.

## High-level components

```
apps/web        Next.js (TypeScript, Tailwind, shadcn/ui, React Flow)
apps/api        FastAPI backend
packages/shared TypeScript types/contracts shared across apps
packages/prompts Agent prompt templates (used once the agent engine lands)
workflows        Workflow graph definitions (JSON), consumed by the API/agents
docs             Product and architecture documentation
```

## Frontend (apps/web)

- **Next.js + TypeScript** — app router.
- **Tailwind CSS** — utility styling.
- **shadcn/ui** — component primitives, added incrementally via the shadcn
  CLI into `components/ui`. `components.json` is already configured.
- **React Flow** (planned dependency, added when the workflow canvas is
  built) — renders the SDLC workflow as an interactive graph, driven by
  `workflows/sdlc-workflow.json` (or the API's serving of it later).

## Backend (apps/api)

- **FastAPI** app in `app/main.py`.
- Folder structure:
  - `app/api/routes/` — route modules (currently just `health.py`).
  - `app/core/` — settings/config.
  - `app/models/` — SQLAlchemy models (empty until persistence is added).
  - `app/schemas/` — Pydantic request/response schemas (empty until first
    real endpoint is added).
- CORS is pre-configured for local frontend dev (`localhost:3000`).

## Database

- **PostgreSQL** is the system of record (projects, stages, artifacts,
  approvals, agent runs).
- **pgvector** extension planned for future RAG over past artifacts —
  not enabled yet.
- No schema/migrations exist yet; `DATABASE_URL` in `app/core/config.py` is a
  placeholder for when persistence is introduced.

## Workflow model

The SDLC workflow is data, not hardcoded logic: `workflows/sdlc-workflow.json`
describes stages as graph **nodes** and allowed transitions as **edges**
(same shape React Flow consumes: `id`, `type`, `data`, `position` for nodes;
`id`, `source`, `target` for edges). Each stage node declares:

- `artifactType` — what artifact the stage produces.
- `requiresHumanApproval` — whether a human must approve before advancing.
- `agentAssisted` — whether an AI agent participates in this stage.

This lets the workflow branch, support rework loops, or run stages in
parallel later, instead of being a fixed linear wizard.

## Agent engine (future)

- **LangGraph** will drive stage execution: given the workflow graph and the
  current project state, it decides which agent runs next, executes it, and
  records the result.
- Agent prompt templates live in `packages/prompts`, versioned alongside the
  rest of the code.

## Cross-cutting (future)

- **RAG**: pgvector-backed retrieval over prior artifacts to ground agent
  drafts in real project history.
- **MCP integrations**: let agents call external tools/systems through the
  Model Context Protocol.
- **Agent logs**: structured, queryable record of every agent
  invocation/output for auditability and debugging.
- **Ops dashboards**: cross-project visibility (stage bottlenecks, pending
  approvals, agent activity).

## Non-goals for now

- No auth/multi-tenancy yet.
- No database migrations yet.
- No agent engine wiring yet.
- No RAG/MCP/logging/dashboards yet — the structure just leaves room for
  them.
