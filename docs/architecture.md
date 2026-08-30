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

## Cross-cutting

- **RAG** — implemented. pgvector-backed retrieval over Knowledge Base
  chunks grounds agent drafts before generation; see
  `app/services/retrieval.py` and `app/services/embeddings.py`.
- **MCP integrations** — foundation only; see the dedicated section below.
- **Agent logs**: structured, queryable record of every agent
  invocation/output for auditability and debugging. Partially covered
  today by `AgentRun` rows and the `AuditLog` table; a dedicated
  structured-log view is still future work.
- **Ops dashboards** — implemented. `GET /ops/summary` and the AI Ops
  Dashboard (`/ops`) give cross-project visibility into agent activity,
  run success/failure, cost, and the human approval gate.

## MCP integrations (planned)

**Status: foundation only.** The `Integration` model
(`app/models/integration.py`) and its CRUD API (`app/api/routes/integrations.py`)
exist so the Settings UI has real data to show, but no MCP client is wired
up, no integration ever actually connects, and `POST /integrations/{id}/connect`
deliberately returns `501 Not Implemented` rather than faking success. This
section describes the intended shape once that changes — nothing below is
built yet.

### Why MCP

The [Model Context Protocol](https://modelcontextprotocol.io) standardizes
how an LLM-driven agent discovers and calls external tools — a Jira "create
issue" action, a Confluence "publish page" action, a GitHub "list open
PRs" action — without this codebase hand-rolling a bespoke client and
auth flow per provider. Anthropic's own agent tooling (the Claude API's
tool-use loop, the Claude Agent SDK) already speaks MCP, so an MCP server
per provider is the natural integration point for this app's agents,
rather than each `AgentDefinition` growing provider-specific code.

### Planned shape

1. **One MCP server per connected provider.** An `Integration` row moving
   to `CONNECTED` corresponds to a running (or reachable) MCP server
   instance configured for that specific connection — e.g. a Jira MCP
   server pointed at one workspace's base URL and project key. Multiple
   `Integration` rows can exist for the same `provider` (e.g. two Jira
   projects), each its own MCP server configuration.
2. **Non-secret config in `Integration.config_json`; credentials in a
   vault.** `config_json` holds things like a base URL, project/workspace
   key, or channel id — never a token, password, or API key. Real
   credentials will be resolved through a secrets manager (or the Claude
   Managed Agents vault pattern, if this app's agent runs move onto that
   platform) and substituted at call time, never persisted in this table
   or logged.
3. **A workflow node opts into the tools it's allowed to use.**
   `WorkflowNode.allowed_actions` already gates what a stage's agent may
   do inside this app (draft/edit/submit/approve/...); the same principle
   extends outward — a stage's `AgentPrompt` will declare which MCP
   tool(s) (if any) its agent may call, so e.g. only the Story Crafting or
   Implementation stages could ever reach a Jira/GitHub tool, and a
   Requirement Intake agent has no path to call one at all.
4. **Every external side effect stays human-reviewable.** Per this
   product's human-in-the-loop principle (see docs/product-vision.md), an
   MCP tool call that changes external state (creating a Jira issue,
   posting to Slack, publishing a Confluence page) will be gated the same
   way an artifact's own stage output is: proposed by the agent, visible
   in the Agent Run Detail view (the same place retrieved Knowledge Base
   sources are shown — see `app/services/retrieval.py`), and only actually
   executed after a human approves it or as an explicit, logged, opt-in
   automation — never a silent side effect of a draft/improve/validate run.
5. **Every call is audited.** Same `AuditLog` mechanism already used for
   every other mutating action in this app
   (`app/services/audit.py`) — an MCP tool invocation records actor,
   action, entity, and outcome, so "what did an agent actually do in Jira"
   is always answerable.

### Planned providers

The five providers seeded as `Integration` placeholders today
(`app/db/seed.py`) are the ones this plan targets first: **Jira**,
**Confluence**, **GitHub**, **Slack** (or **Teams** — one chat provider is
expected to be enabled per organization, not both), and **Azure DevOps**.
Likely first uses, once connected:

| Provider | Likely first use |
|---|---|
| Jira | Push an approved Story Crafting backlog as Jira issues (see the Export Stories feature, which already produces a clean structured export — the natural predecessor to a real push) |
| Confluence | Publish an approved artifact (HLD, requirements) as a page |
| GitHub | Link Implementation-stage work to commits/PRs; read PR/CI status back into a Testing-stage agent's context |
| Slack / Teams | Notify a channel when a review is pending or decided, or a release ships |
| Azure DevOps | Sync work items and pipeline status, for orgs standardized on Azure over Jira/GitHub |

### Non-goals for now

- No real MCP client library is integrated yet.
- No integration ever actually authenticates or connects — every seeded
  row is `NOT_CONNECTED` and stays that way until this plan is executed.
- No secrets/credentials are stored anywhere in this codebase today.

## Non-goals for now

- No auth/multi-tenancy yet.
- No agent-log structured view yet (raw `AgentRun`/`AuditLog` rows exist;
  a dedicated queryable log UI doesn't).
- No real MCP tool wiring yet — see the dedicated section above.
