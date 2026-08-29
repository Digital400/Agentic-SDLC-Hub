# MVP Plan — Agentic SDLC Hub

This plan defines what the MVP delivers, who uses it, how they use it, and
how we'll know it succeeded. It builds on the vision and scope defined in
[docs/product-vision.md](product-vision.md).

Principle: build the smallest thing that proves the core loop end-to-end,
then extend. Nothing below is implemented yet unless marked done — this is
the roadmap the step-by-step build follows.

## 1. MVP features

- [x] Monorepo foundation: frontend (Next.js), backend (FastAPI), shared
      packages, workflow definition, and documentation.
- [ ] **Project management** — create a project; it is assigned the default
      SDLC workflow graph.
- [ ] **Workflow visualization** — a graph view (React Flow) of a project's
      stages, showing current stage and per-stage status (not started,
      drafted, pending approval, approved).
- [ ] **Artifact drafting** — for agent-assisted stages, request an
      AI-drafted artifact (a single, direct LLM call — no multi-agent
      orchestration in the MVP); the draft can also be authored or edited
      by a human.
- [ ] **Approval workflow** — for stages that require human approval,
      submit the artifact for review and record an explicit approve/reject
      decision, with the ability to send it back for revision.
- [ ] **Stage progression** — a project automatically advances to the next
      stage in the workflow graph once its current stage's requirements
      (artifact produced, approval recorded if required) are met.
- [ ] **Persistence** — projects, stages, artifacts, and approval decisions
      are stored durably in PostgreSQL and remain inspectable after the
      fact.

## 2. User roles

The MVP recognizes two functional roles (not enforced by authentication
yet — see Out of scope):

- **Contributor** — creates projects, triggers agent drafts, authors or
  edits artifacts, and moves work forward within a stage.
- **Approver** — reviews artifacts on stages that require human approval
  and records the approve/reject decision. In the MVP, any user may act as
  an approver; formal role-based restriction is future scope.

Leadership and other stakeholders are **viewers** of project and workflow
status by using the same interface, without needing a distinct role in the
MVP.

## 3. Main user journey

1. A contributor creates a new project. It starts at the **Requirements**
   stage of the default workflow.
2. The contributor requests an AI-drafted requirements document, then edits
   it as needed.
3. The contributor submits the requirements document for approval.
4. An approver reviews it in the platform and either **approves** it
   (project advances to **Design**) or **rejects** it with feedback
   (artifact returns for revision).
5. This draft → review → approve/reject → advance cycle repeats through
   **Design**, **Implementation** (no approval gate), **Testing**, and
   **Deployment**.
6. At any point, any user can open the project's workflow graph and see
   which stage it's in, what artifacts exist, and what is pending approval.
7. Once **Deployment** is approved, the project is complete, and its full
   artifact and approval history remains available for reference.

## 4. Success criteria

The MVP is successful if:

- A project can be taken from creation through all five default stages to
  completion entirely within the platform, with no artifact or approval
  tracked outside it.
- Every artifact and approval decision is durably recorded and can be
  reviewed after the fact (who produced it, who approved it, when).
- A human approval is required — and enforced by the system, not just by
  convention — before any approval-gated stage advances.
- At least one agent-assisted stage measurably reduces drafting time
  compared to writing the artifact from scratch.
- A non-technical stakeholder can open the workflow graph view and
  correctly identify a project's current stage and any pending approvals
  without needing an explanation.

## 5. Risks

- **AI draft quality is inconsistent.** A poor first draft could slow
  contributors down rather than speed them up.
  *Mitigation:* keep drafts easily editable; treat the agent as a starting
  point, not a final answer, and gather feedback to improve prompts
  ([packages/prompts](../packages/prompts)) over time.
- **Approval becomes a rubber stamp.** If reviewing is harder than writing,
  approvers may approve without real scrutiny, undermining the
  human-in-the-loop principle.
  *Mitigation:* keep the approval UI focused on the actual artifact content
  and any agent involvement, so review is fast but meaningful.
- **Workflow rigidity mismatches real projects.** The default linear
  workflow may not fit every project's needs (e.g. rework, skipped
  stages).
  *Mitigation:* the workflow is graph-based data from day one
  ([workflows/sdlc-workflow.json](../workflows/sdlc-workflow.json)), so
  branching and rework paths can be added without a redesign — just not in
  the MVP.
- **Scope creep before the core loop is proven.** Pressure to add RAG,
  dashboards, or integrations before the basic loop works could delay
  validation.
  *Mitigation:* explicit "out of scope for MVP" list in the product vision,
  revisited only after MVP success criteria are met.
- **No access control in the MVP.** Any user can approve any artifact,
  which is unsuitable for production use beyond an internal pilot.
  *Mitigation:* scope the MVP to a trusted pilot group; prioritize
  authentication/authorization immediately after MVP validation.

## 6. Assumptions

- A single default SDLC workflow (Requirements → Design → Implementation →
  Testing → Deployment) is representative enough of real projects to
  validate the concept.
- One direct LLM call per agent-assisted stage is sufficient for MVP draft
  quality; multi-agent orchestration (LangGraph) is not required to prove
  the core loop.
- The MVP will be used by a small, trusted internal pilot group, so the
  absence of authentication/authorization and role enforcement is
  acceptable temporarily.
- PostgreSQL alone (without pgvector) is sufficient for MVP persistence;
  no retrieval/grounding over historical artifacts is needed yet.
- Users have basic familiarity with SDLC concepts (requirements, design,
  testing, deployment) and do not need in-product process education.
- Local/single-environment deployment is sufficient for the MVP; production
  hardening (scaling, observability, backups) is out of scope until after
  validation.

## Suggested build order

1. ✅ Monorepo + frontend/backend foundation.
2. ✅ Product documentation (vision, MVP plan).
3. Database schema + migrations for `projects`, `sdlc_stages`, `artifacts`,
   `approvals`.
4. API endpoints for projects and the workflow graph (read-only workflow to
   start).
5. Web: project list + create project (assigns the default workflow).
6. Web: React Flow canvas showing a project's stages and status.
7. API + Web: artifact create/edit + submit-for-approval + approve/reject.
8. Wire in one agent-assisted draft (single LLM call, no LangGraph yet).
9. Revisit against the success criteria above before adding anything from
   the future roadmap.

Each step should land as a reviewable, runnable increment — not a big-bang
merge.
