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

This repository currently contains the **foundation only** — frontend and
backend scaffolding, folder structure, and a health check. No business
features yet.

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

## Status

Foundation only. No business features implemented yet — see
[docs/mvp-plan.md](docs/mvp-plan.md) for what's next.
