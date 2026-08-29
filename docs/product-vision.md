# Product Vision — Agentic SDLC Hub

## 1. Product name

**Agentic SDLC Hub**

## 2. Problem statement

Software delivery inside the company currently runs through a patchwork of
documents, chat threads, tickets, and informal approvals. Each team follows
its own version of the Software Development Life Cycle (SDLC), and the
artifacts that record what was decided — requirements, designs, test
results, deployment sign-offs — live in disconnected tools or nowhere at
all.

This creates three recurring problems:

- **No consistent process.** Leadership cannot see, at a glance, what stage
  a project is in or what has actually been reviewed and approved.
- **No durable audit trail.** When something goes wrong, it's difficult to
  reconstruct who decided what, when, and on what basis.
- **Slow, manual drafting.** Producing requirements docs, design docs, and
  test reports is time-consuming, and that time is rarely spent on judgment
  — it's spent on writing the first draft.

Agentic SDLC Hub addresses all three by giving every project a single,
structured workflow, a durable record of every artifact and approval, and
AI agents that remove the drafting burden without removing human
accountability.

## 3. Target company users

- **Engineering teams** (developers, tech leads) — run their projects
  through the platform and use agents to accelerate artifact drafting.
- **Engineering managers / project leads** — track project status across
  the SDLC and review/approve key artifacts.
- **QA and release stakeholders** — review test reports and deployment
  artifacts before sign-off.
- **Leadership / delivery management** — gain visibility into where every
  project stands and how consistently the process is being followed,
  without chasing individual teams for status.

## 4. Main value proposition

Agentic SDLC Hub lets the company **move faster without losing control**:

- AI agents do the drafting work at each SDLC stage, so teams spend their
  time reviewing and deciding rather than writing from a blank page.
- Every stage still requires the right artifact and, where it matters, an
  explicit human approval — the process cannot be silently skipped.
- Every project's history — what was produced, by whom (agent or human),
  and who approved it — is recorded and inspectable, giving leadership a
  single source of truth for delivery status and audit needs.

## 5. Core SDLC process

Every project moves through a defined sequence of stages. Each stage:

1. Produces a specific **artifact** (a document or record — not just a
   status update).
2. May be **agent-assisted**, meaning an AI agent can produce a first draft
   of that artifact.
3. May **require human approval** before the project can advance to the
   next stage.

The default workflow is:

| Stage | Artifact | Agent-assisted | Requires human approval |
|---|---|---|---|
| Requirements | Requirements document | Yes | Yes |
| Design | Design document | Yes | Yes |
| Implementation | Code change | Yes | No |
| Testing | Test report | Yes | Yes |
| Deployment | Deployment record | No | Yes |

Critically, this process is modeled as a **graph of stages and transitions**,
not a fixed linear wizard. This means the workflow can later support
branching, rework loops (e.g. sending a project back from Testing to
Implementation), or parallel stages — without redesigning the platform.

## 6. Human-in-the-loop principle

AI agents accelerate work; **humans remain accountable for outcomes.**

- Any artifact that materially affects the direction, quality, or release
  of a project requires a named human to approve it before the project
  proceeds.
- Approval is an explicit, recorded action — not an assumption or a
  timeout.
- Humans can edit an agent's draft before approving it; approval always
  applies to the final, human-reviewed version of the artifact, not the
  agent's raw output.

This principle exists so that adopting AI assistance never means adopting
AI decision-making. The platform accelerates drafting; it does not
delegate judgment.

## 7. AI agent principle

AI agents in Agentic SDLC Hub operate strictly within three roles:

- **Draft** — produce a first version of a stage's artifact from the
  available project context.
- **Improve** — revise or refine an artifact based on human feedback.
- **Validate** — flag gaps, inconsistencies, or risks in an artifact for a
  human to consider.

Agents do not have authority to approve artifacts, advance a project to the
next stage, or make a change final. Every agent action is attributable and
recorded, so its contribution to any artifact can be traced later.

## 8. MVP scope

The MVP proves the core loop end-to-end, using the default workflow above:

- Create a project on the default SDLC workflow.
- Produce an artifact for a stage (agent-drafted or human-authored).
- Require and record human approval on stages that need it.
- Advance the project to the next stage once approved.
- Provide a visual, graph-based view of a project's workflow and status.

## 9. Out of scope for MVP

- Multiple or custom workflow templates, and any workflow-editing UI
  (ship with one default workflow graph).
- Authentication, authorization, and multi-tenancy.
- Retrieval-augmented generation (RAG) over historical projects/artifacts.
- MCP integrations with external tools/systems.
- Agent execution logs and observability tooling.
- Cross-project ops dashboards.
- Multi-agent orchestration (LangGraph); MVP agents are single, direct
  calls per stage.
- Notifications (email/chat) and reminders.

## 10. Future roadmap

Once the MVP loop is validated, the platform is intended to grow along
these lines, roughly in order:

1. **Richer workflows** — support multiple workflow templates per project
   type, and allow rework loops / conditional branches in the graph.
2. **Retrieval-augmented generation (RAG)** — ground agent drafts in the
   company's own historical artifacts, using pgvector.
3. **MCP integrations** — let agents call out to external systems (issue
   trackers, CI/CD, code hosts) as part of drafting or validating
   artifacts.
4. **Agent execution logs** — a structured, queryable record of every agent
   invocation, input, and output for auditability, debugging, and trust.
5. **Ops dashboards** — cross-project visibility for leadership: stage
   bottlenecks, pending approvals, agent activity, and delivery trends.
6. **Multi-agent orchestration (LangGraph)** — coordinate multiple
   specialized agents within a stage as workflows and agent
   responsibilities grow more complex.
7. **Access control and multi-tenancy** — role-based permissions as the
   platform scales beyond initial pilot teams.
