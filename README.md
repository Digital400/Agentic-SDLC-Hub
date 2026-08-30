# Agentic SDLC Hub

## 1. Product purpose

Agentic SDLC Hub is a company-wide platform that runs every software project
through a controlled, graph-based Software Development Life Cycle (SDLC),
assisted by AI agents.

- Every project follows a defined SDLC workflow (graph of stages, not a
  fixed linear wizard).
- Every stage produces a concrete **artifact** (requirements doc, design
  doc, test report, etc.).
- AI agents **draft, improve, and validate** artifacts.
- Humans **approve** the important artifacts before a project advances.
- The platform is built to grow into RAG, MCP integrations, agent execution
  logs, and ops dashboards.

See [docs/product-vision.md](docs/product-vision.md) for the full vision and
[docs/architecture.md](docs/architecture.md) for the technical design.

## 2. MVP scope

The MVP proves one loop end-to-end: create a project → an agent-assisted
stage produces an artifact → a human approves it → the project advances to
the next stage in the workflow graph.

Full scope and build order: [docs/mvp-plan.md](docs/mvp-plan.md).

The full MVP loop described above is implemented end-to-end — see
[§6, MVP demo flow](#6-mvp-demo-flow) below to run it yourself, and
[§9, known limitations](#9-known-limitations) for what's deliberately still
a placeholder.

## 3. How to run the frontend

This is a Yarn workspaces monorepo — run `yarn install` once from the
**repo root** (not inside `apps/web`), then run the dev server from
`apps/web`:

```bash
yarn install          # from the repo root, once
cd apps/web
yarn dev
```

Visit http://localhost:3000.

Stack: Next.js (App Router) + TypeScript + Tailwind CSS, with a
shadcn/ui-ready structure (`components.json` configured, `components/ui/`
ready for generated components — run `yarn dlx shadcn@latest init` /
`yarn dlx shadcn@latest add <component>` from `apps/web` when you start
adding UI components).

> Package manager: this repo uses **Yarn (Berry, PnP)** — pinned via
> `packageManager` in the root `package.json`. Don't use `npm`/`npx` here;
> it will generate a conflicting `node_modules`/`package-lock.json` next to
> Yarn's PnP setup (`.pnp.cjs`, `.yarn/`, `yarn.lock`).

## 4. How to run the backend

**PowerShell (Windows):**

```powershell
cd apps/api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**bash (macOS/Linux/Git Bash):**

```bash
cd apps/api
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Health check: http://localhost:8000/health → `{"status": "ok"}`
Interactive API docs: http://localhost:8000/docs

Stack: FastAPI, with a clean folder structure (`app/api/routes`,
`app/core`, `app/models`, `app/schemas`) ready for real endpoints and a
database layer.

Copy `apps/api/.env.example` to `apps/api/.env` to override defaults (e.g.
`DATABASE_URL`) locally.

## 5. Folder explanation

```
agentic-sdlc-hub
├── apps
│   ├── web          Next.js + TypeScript + Tailwind + shadcn/ui frontend
│   └── api          FastAPI backend
├── packages
│   ├── shared       Shared TypeScript types/contracts used across apps
│   └── prompts      Agent prompt templates (for the future agent engine)
├── workflows
│   └── sdlc-workflow.json   Default SDLC workflow as a graph (stages + transitions)
├── docs
│   ├── product-vision.md    What this platform is and why
│   ├── architecture.md      Technical design and future direction
│   └── mvp-plan.md          MVP scope and build order
└── README.md
```

- **apps/web** — the user-facing app. Will host the workflow canvas
  (React Flow), project views, and artifact review/approval UI.
- **apps/api** — the backend of record. Owns the database, workflow logic,
  and (later) agent invocation.
- **packages/shared** — cross-cutting TypeScript types (e.g. artifact/stage
  shapes) so frontend code doesn't drift from backend contracts.
- **packages/prompts** — versioned prompt templates for AI agents, kept
  separate from application code.
- **workflows** — workflow definitions as data (JSON graphs), not hardcoded
  in application logic, so new workflows can be added without code changes.
- **docs** — living documentation for product intent, architecture, and
  delivery plan.

## 6. MVP demo flow

The shortest path that exercises every piece of the platform, end to end.
Run both apps first (§3/§4), then seed sample data:

```bash
cd apps/api
python -m app.db.seed
```

This creates 3 users with real roles (an Admin, a BA, and a Product Owner —
see §10 below), 11 agent definitions with default prompts, a sample project
partway through its workflow, a few Knowledge Base sources with real
embeddings, and a demo Story Crafting backlog. Then, in the browser:

1. **Create a project** — Projects → New Project. Generates the 11-stage
   workflow graph automatically; the first stage (Requirement Intake)
   starts unlocked.
2. **Add a requirement & run the agent** — open the Workflow tab, click the
   Requirement Intake node, type a stakeholder request, click **Run
   Agent**. This both runs the agent and saves its output as v1 of the
   artifact in one action, then takes you to the document editor.
3. **Review the draft** — in the editor, edit the sections if you like,
   then **Send for review** (this both submits the artifact and opens the
   review round — see §9 for why that used to be two separate, easy-to-miss
   steps).
4. **Approve it** — Reviews → open the pending review → check the
   reviewer checklist → **Approve**. The next stage (Problem Discovery)
   unlocks automatically.
5. **Repeat Run Agent → Send for review → Approve** through Problem
   Discovery, Solution Discovery, and HLD.
6. **Run the Story Crafting agent**, approve the backlog, then open it and
   try **Export Stories** (Markdown/CSV/JSON) and **Preview Jira Export** —
   both work against the same parsed backlog; neither talks to a real
   external system yet.
7. **Check retrieval worked** — open any completed run's **Agent Run
   Detail** page (linked from the dashboard's "Recent agent runs" or a
   document's Run Agent result) and look at "Retrieved knowledge sources" —
   if your stakeholder request/project domain matched a seeded Knowledge
   Base source, it's cited there and in the drafted text itself.
8. **Check the AI Ops Dashboard** (`/ops`) — run counts, cost, approval
   rate, and the stage performance table should all reflect what you just
   did.
9. **Check permissions** — try approving a review as a BA (should be
   rejected with a 403 and a clear message) vs. the seeded Product Owner
   (should succeed). No login exists yet (§9), so "acting as" a given role
   means calling the API with that user's id directly, or via `curl` — see
   `apps/api/app/services/permissions.py` for the full rule set.

## 7. Test checklist

Use this to sanity-check a change before calling it done. Each line is a
concrete, verifiable action, not a vibe check.

- **Project creation** — creating a project generates all 11 workflow
  nodes + edges; the first node is `IN_PROGRESS`, every other node is
  `NOT_STARTED`; a whitespace-only name/business owner is rejected (422).
- **Workflow graph generation** — the graph renders in React Flow with the
  right node count/edges; clicking a node with no artifact yet shows the
  Run Agent form; clicking one with an artifact links straight to it.
- **Artifact creation** — an artifact can only be created/edited by a role
  in `STAGE_EDIT_ROLES` for its stage (see `permissions.py`); creating one
  for a `workflow_node_id` that doesn't belong to the given project is
  rejected (400).
- **Version history** — editing a `DRAFT` artifact in place does **not**
  bump the version number; `POST .../versions` always does, and resets
  status back to `DRAFT` even if the artifact was `APPROVED`.
- **Review gate** — an artifact must be `READY_FOR_REVIEW` before a review
  can be opened; approving unlocks every downstream node whose other
  prerequisites are already satisfied, not just the immediate next one;
  a decision from a role outside `STAGE_APPROVE_ROLES` is rejected (403).
- **Agent prompt library** — editing the *active* version in place is
  rejected (409) — it must go through "save as new version" instead;
  activating a version deactivates every sibling in the same
  (agent, role) lineage.
- **Agent run flow** — a run against a stage whose required upstream
  artifact isn't `APPROVED` yet fails cleanly (`FAILED` status + a
  human-readable `error_message`), never a 500; running with no
  `ANTHROPIC_API_KEY`/`GEMINI_API_KEY` configured still produces
  deterministic mock output.
- **RAG foundation** — `POST /knowledge-sources/search` with an obviously
  unrelated query returns `[]`, not noise; a run's `retrieved_sources`
  matches what the drafted text actually cites.
- **Ops dashboard** — the 10 metric cards render correctly with zero data
  (new/empty database) — no `NaN`/`Infinity`/divide-by-zero anywhere.
- **Permissions** — every one of the 5 gated actions (project update,
  artifact edit, review decision, agent run, prompt update) rejects a
  role outside its allowed set with a 403 naming the allowed roles, and an
  Admin actor bypasses every check.

## 8. What was reviewed and hardened in this pass

- **Broken/missing UI states** — added a global `app/loading.tsx` and
  `app/error.tsx` (plus `global-error.tsx` for a root-layout crash); every
  route previously showed a blank page during data fetching and Next's
  generic unbranded page on any unhandled error.
- **Weak validation** — `Field(min_length=1)` accepted whitespace-only
  strings (e.g. a project literally named `"   "`). Added
  `app/schemas/validators.py`'s `NonBlankStr` (strips, then requires a real
  character) and applied it to every required human-typed field: project
  name/business owner, artifact title/content, prompt name/stage/system
  prompt/output format, knowledge source title/category/chunk content,
  integration name, and review comments.
- **Inconsistent naming** — the Workflow Graph's "assigned role" per stage
  (e.g. "Product Manager") was a leftover from before the permission
  system existed and didn't match any role `permissions.py` actually
  checks; it now shows the real role(s) allowed to edit that stage (e.g.
  "BA", "Architect / Tech Lead"). The header also showed a hardcoded
  "Suru Sampathi / Owner" regardless of who the default acting user
  actually was post-RBAC; it now renders the real user and role.
- **Poor mobile/tablet layout** — the Workflow Graph's canvas+detail-panel
  row and the Document Editor's 3-column layout both used fixed-width side
  panels that would crush the main content on a phone/tablet-portrait
  screen; both now stack vertically below the `lg` breakpoint.
- **Missing audit logs** — audited every mutating endpoint against its
  `record_audit_log` call; all of them already had one. No gap found.

## 9. Known limitations

- **No authentication or sessions.** Every "who's doing this" check
  (audit trail, permissions, "created by") takes an explicit user id from
  the request itself, not a login. The frontend defaults to "the first
  seeded user" wherever a UI needs to act as somebody. This is a
  deliberate MVP scope cut (see [docs/mvp-plan.md](docs/mvp-plan.md)), not
  an oversight — but it means the permission system (§10) is real and
  enforced, just not yet tied to a real signed-in identity.
- **Real AI generation is optional and provider-limited.** Falls back to
  deterministic mock output with no key configured; supports Anthropic
  Claude or Google Gemini (free tier), Anthropic taking priority if both
  are set. No other providers.
- **RAG retrieval isn't a real embedding model.** `embed_text` uses the
  hashing trick (a real, legitimate lightweight technique — not fake) as a
  zero-dependency stand-in; it's noticeably coarser than a real embedding
  model and was empirically threshold-tuned against a handful of test
  queries, not a benchmark.
- **No real external integrations.** Jira/Confluence/GitHub/Slack/Teams/
  Azure DevOps are placeholder rows (`Integration.status` is always
  `NOT_CONNECTED`); `POST /integrations/{id}/connect` returns 501 on
  purpose. The Jira export preview maps fields correctly but never pushes
  anywhere.
- **Several permission rules are inferred, not specified.** Only BA's
  edit rule and the five named approval rules came from the product spec
  verbatim; every other stage's edit role and both non-stage rules
  (project update, prompt update) are reasonable defaults flagged inline
  in `app/services/permissions.py` — confirm they match intent before
  relying on them.
- **No per-project role overrides.** A user's role is global; the same
  person can't be a BA on one project and a Viewer on another yet.
- **No structured, queryable agent-log view.** `AgentRun` rows and
  `AuditLog` exist and are complete, but there's no dedicated UI beyond
  the Agent Run Detail page and the Ops Dashboard's recent-runs table.
- **No file upload pipeline.** Knowledge Base sources/chunks are created
  via API with `content` supplied directly — there's no "drag in a PDF"
  flow yet, so the Knowledge Base UI's "Upload document" button is a
  placeholder.

## 10. Permissions quick reference

Nine global roles (`app/models/enums.py`'s `UserRole`); Admin bypasses
every check, Viewer is denied every mutating action by construction. Full
rule set — including which are spec'd vs. inferred defaults — lives in
`app/services/permissions.py`.

| Role | Can do |
|---|---|
| Admin | Everything |
| BA | Create/edit Requirement Intake & Problem Discovery docs |
| Product Owner | Approve Requirement, Problem, Solution, Stories; update project settings |
| Architect | Approve HLD |
| Tech Lead | Approve LLD & Implementation |
| QA | Approve Test Plan |
| DevOps | Approve Infrastructure & Release readiness |
| Developer, Viewer | Read-only for approvals/project settings (Developer can edit Implementation/Maintenance stages) |

## 11. Next roadmap

Roughly in priority order:

1. **Real authentication** (sessions or SSO) — the single biggest gap;
   unlocks a real "current user" instead of the first-seeded-user default
   everywhere, and makes the permission system fully meaningful.
2. **A real embedding model** behind the same `embed_text`/`embed_texts`
   functions (e.g. Voyage AI, which Anthropic recommends) — no other RAG
   code should need to change.
3. **A real MCP client** for at least one provider (Jira is the natural
   first, given the export-preview groundwork already in place) — see
   [docs/architecture.md](docs/architecture.md)'s MCP integrations section
   for the intended shape (per-stage tool authorization, human-review
   gating on any external side effect, full audit logging).
4. **Per-project role assignment**, on top of today's global role.
5. **A file upload pipeline** for the Knowledge Base (parsing + chunking),
   replacing today's direct-content-only creation API.
6. **A structured agent-log / observability view** beyond the Ops
   Dashboard's summary table — full request/response inspection per run.
7. **Notifications** — the Settings page's notification preferences are
   already stubbed in the UI; nothing sends one yet.
8. **Cost-over-time charting** — the Ops Dashboard's cost card is an
   explicit placeholder pending per-day cost aggregation.

## Status

MVP loop fully implemented and demoable — see §6 above. Still missing
real auth, a real embedding model, and real external integrations by
design; see §9 for the complete list.
