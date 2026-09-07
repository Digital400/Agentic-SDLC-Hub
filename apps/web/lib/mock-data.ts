import type { WorkflowStatus } from "@agentic-sdlc-hub/shared";
import type {
  AgentDefinitionSummary,
  AgentPromptVersion,
  AgentRunSummary,
  DocumentArtifact,
  Project,
  ProjectWorkflowEdge,
  ProjectWorkflowNode,
  ReviewChecklistItem,
  ReviewItem,
} from "@/lib/types";

const FULL_REVIEW_ACTIONS = ["draft", "edit", "submit_for_review", "approve", "reject", "request_changes"];
const NO_REVIEW_ACTIONS = ["draft", "edit", "submit_for_review", "complete"];

// Mirrors workflows/sdlc-workflow.json's 12 stages exactly (name,
// description, agentKey, requiredInputs, outputArtifactType,
// requiresHumanApproval, allowedActions, position) so the mock Workflow
// page matches the real template. `assignedRole` is a frontend-only
// convenience (the human role expected to act on that stage) — not part
// of the backend template. Only `status` varies per project — see
// buildWorkflowNodes below.
const TEMPLATE_STAGES = [
  { key: "requirement_intake", name: "Requirement Intake", description: "Capture the initial stakeholder request, desired outcome, and any known constraints before analysis begins.", agentKey: "requirement-intake-agent", assignedRole: "Product Manager", requiredInputs: ["stakeholder_request"], outputArtifactType: "intake_summary", requiresHumanApproval: true, allowedActions: FULL_REVIEW_ACTIONS, x: 0 },
  { key: "problem_discovery", name: "Problem Discovery", description: "Investigate the underlying problem, its impact, and constraints, and state it clearly enough to design against.", agentKey: "problem-discovery-agent", assignedRole: "Product Manager", requiredInputs: ["intake_summary"], outputArtifactType: "problem_statement", requiresHumanApproval: true, allowedActions: FULL_REVIEW_ACTIONS, x: 220 },
  { key: "solution_discovery", name: "Solution Discovery", description: "Explore candidate solution approaches, weigh trade-offs, and recommend one to move forward with.", agentKey: "solution-discovery-agent", assignedRole: "Tech Lead", requiredInputs: ["problem_statement"], outputArtifactType: "solution_options_doc", requiresHumanApproval: true, allowedActions: FULL_REVIEW_ACTIONS, x: 440 },
  { key: "hld", name: "High-Level Design", description: "Define the system-level architecture and major components for the chosen solution.", agentKey: "hld-agent", assignedRole: "Tech Lead", requiredInputs: ["solution_options_doc"], outputArtifactType: "hld_document", requiresHumanApproval: true, allowedActions: FULL_REVIEW_ACTIONS, x: 660 },
  { key: "story_crafting", name: "Story Crafting", description: "Break the approved high-level design into implementable stories/tasks with acceptance criteria.", agentKey: "story-crafting-agent", assignedRole: "Product Manager", requiredInputs: ["hld_document"], outputArtifactType: "story_backlog", requiresHumanApproval: true, allowedActions: FULL_REVIEW_ACTIONS, x: 880 },
  { key: "lld", name: "Low-Level Design", description: "Detail module/component-level design, data models, and interfaces for the stories in scope.", agentKey: "lld-agent", assignedRole: "Tech Lead", requiredInputs: ["story_backlog"], outputArtifactType: "lld_document", requiresHumanApproval: true, allowedActions: FULL_REVIEW_ACTIONS, x: 1100 },
  { key: "implementation", name: "Implementation", description: "Write the code changes that satisfy the low-level design and the in-scope stories' acceptance criteria.", agentKey: "implementation-agent", assignedRole: "Software Engineer", requiredInputs: ["lld_document", "story_backlog"], outputArtifactType: "code_change", requiresHumanApproval: false, allowedActions: NO_REVIEW_ACTIONS, x: 1320 },
  { key: "pr_review", name: "PR Review", description: "Review the implementation's code change for correctness, design alignment, and risk before it moves to testing.", agentKey: "pr-review-agent", assignedRole: "Tech Lead", requiredInputs: ["code_change"], outputArtifactType: "pr_review_report", requiresHumanApproval: true, allowedActions: FULL_REVIEW_ACTIONS, x: 1430 },
  { key: "testing", name: "Testing", description: "Validate the implementation against acceptance criteria and produce a test report with documented evidence.", agentKey: "testing-agent", assignedRole: "QA Engineer", requiredInputs: ["code_change"], outputArtifactType: "test_report", requiresHumanApproval: true, allowedActions: FULL_REVIEW_ACTIONS, x: 1540 },
  { key: "infrastructure", name: "Infrastructure Provisioning", description: "Provision and configure the infrastructure and environments required to release the change.", agentKey: "infrastructure-agent", assignedRole: "DevOps Engineer", requiredInputs: ["test_report"], outputArtifactType: "infrastructure_plan", requiresHumanApproval: true, allowedActions: FULL_REVIEW_ACTIONS, x: 1760 },
  { key: "release", name: "Release", description: "Deploy the change to production and record the deployment outcome.", agentKey: "release-agent", assignedRole: "Release Manager", requiredInputs: ["infrastructure_plan"], outputArtifactType: "deployment_record", requiresHumanApproval: true, allowedActions: FULL_REVIEW_ACTIONS, x: 1980 },
  { key: "maintenance", name: "Maintenance", description: "Monitor the released change, capture incidents and feedback, and feed learnings back into future intake.", agentKey: "maintenance-agent", assignedRole: "Support Engineer", requiredInputs: ["deployment_record"], outputArtifactType: "maintenance_log", requiresHumanApproval: false, allowedActions: NO_REVIEW_ACTIONS, x: 2200 },
] as const;

function buildWorkflowNodes(projectId: string, statuses: WorkflowStatus[]): ProjectWorkflowNode[] {
  return TEMPLATE_STAGES.map((stage, i) => ({
    id: `${projectId}-node-${stage.key}`,
    projectId,
    nodeKey: stage.key,
    name: stage.name,
    description: stage.description,
    agentKey: stage.agentKey,
    assignedRole: stage.assignedRole,
    requiredInputs: [...stage.requiredInputs],
    outputArtifactType: stage.outputArtifactType,
    requiresHumanApproval: stage.requiresHumanApproval,
    allowedActions: [...stage.allowedActions],
    status: statuses[i] ?? "LOCKED",
    blockedReason: null,
    overrideReason: null,
    contextTokenBudget: 8000,
    outputTokenBudget: 4096,
    fullContentArtifactTypes: [],
    ragTopK: 5,
    maxRagTokens: 2000,
    requiredEvidenceSection: stage.key === "testing" ? "Test Evidence" : null,
    orderIndex: i,
    position: { x: stage.x, y: 0 },
  }));
}

function buildWorkflowEdges(projectId: string): ProjectWorkflowEdge[] {
  const edges: ProjectWorkflowEdge[] = [];
  for (let i = 0; i < TEMPLATE_STAGES.length - 1; i++) {
    edges.push({
      id: `${projectId}-edge-${i}`,
      source: `${projectId}-node-${TEMPLATE_STAGES[i].key}`,
      target: `${projectId}-node-${TEMPLATE_STAGES[i + 1].key}`,
    });
  }
  // Testing's rework loop back to Implementation, matching the real template.
  edges.push({
    id: `${projectId}-edge-rework`,
    source: `${projectId}-node-testing`,
    target: `${projectId}-node-implementation`,
    label: "rework",
  });
  return edges;
}

export const mockProjects: Project[] = [
  {
    id: "proj-loyalty",
    name: "Customer Loyalty Rewards Platform",
    description: "Add a points-based loyalty rewards program to the customer mobile app.",
    businessOwner: "Marketing — Jordan Lee",
    currentStage: "problem_discovery",
    status: "ACTIVE",
    workType: "NEW_PROJECT",
    workflowTemplateId: "default-sdlc-workflow",
    createdAt: "2026-08-27T10:00:00Z",
    updatedAt: "2026-08-28T09:30:00Z",
  },
  {
    id: "proj-vendor",
    name: "Vendor Onboarding Portal",
    description: "Self-serve portal for new vendors to submit compliance documents.",
    businessOwner: "Procurement — Sam Rivera",
    currentStage: "hld",
    status: "ACTIVE",
    workType: "NEW_PROJECT",
    workflowTemplateId: "default-sdlc-workflow",
    createdAt: "2026-08-20T14:00:00Z",
    updatedAt: "2026-08-29T11:15:00Z",
  },
  {
    id: "proj-fraud",
    name: "Real-Time Fraud Alerts",
    description: "Push notifications when suspicious card activity is detected.",
    businessOwner: "Risk — Elena Kade",
    currentStage: "testing",
    status: "ACTIVE",
    workType: "NEW_PROJECT",
    workflowTemplateId: "default-sdlc-workflow",
    createdAt: "2026-07-30T08:00:00Z",
    updatedAt: "2026-08-29T16:45:00Z",
  },
  {
    id: "proj-invoicing",
    name: "Automated Invoicing v2",
    description: "Replace the legacy invoicing batch job with an event-driven pipeline.",
    businessOwner: "Finance — Marcus Webb",
    currentStage: "maintenance",
    status: "COMPLETED",
    workType: "NEW_PROJECT",
    workflowTemplateId: "default-sdlc-workflow",
    createdAt: "2026-05-01T09:00:00Z",
    updatedAt: "2026-07-10T12:00:00Z",
  },
  {
    id: "proj-legacy-crm",
    name: "Legacy CRM Data Migration",
    description: "One-time migration of the on-prem CRM into the cloud data warehouse.",
    businessOwner: "Sales Ops — Priya Nair",
    currentStage: "solution_discovery",
    status: "ARCHIVED",
    workType: "NEW_PROJECT",
    workflowTemplateId: "default-sdlc-workflow",
    createdAt: "2026-03-15T09:00:00Z",
    updatedAt: "2026-04-02T09:00:00Z",
  },
];

export const mockWorkflowNodesByProject: Record<string, ProjectWorkflowNode[]> = {
  "proj-loyalty": buildWorkflowNodes("proj-loyalty", [
    "COMPLETED", "READY", "LOCKED", "LOCKED", "LOCKED",
    "LOCKED", "LOCKED", "LOCKED", "LOCKED", "LOCKED", "LOCKED",
  ]),
  "proj-vendor": buildWorkflowNodes("proj-vendor", [
    "COMPLETED", "COMPLETED", "COMPLETED", "WAITING_FOR_REVIEW", "LOCKED",
    "LOCKED", "LOCKED", "LOCKED", "LOCKED", "LOCKED", "LOCKED",
  ]),
  "proj-fraud": buildWorkflowNodes("proj-fraud", [
    "COMPLETED", "COMPLETED", "COMPLETED", "COMPLETED", "COMPLETED",
    "COMPLETED", "COMPLETED", "READY", "LOCKED", "LOCKED", "LOCKED",
  ]),
  "proj-invoicing": buildWorkflowNodes("proj-invoicing", Array(11).fill("COMPLETED") as WorkflowStatus[]),
  "proj-legacy-crm": buildWorkflowNodes("proj-legacy-crm", [
    "COMPLETED", "COMPLETED", "BLOCKED", "LOCKED", "LOCKED",
    "LOCKED", "LOCKED", "LOCKED", "LOCKED", "LOCKED", "LOCKED",
  ]),
};

export const mockWorkflowEdgesByProject: Record<string, ProjectWorkflowEdge[]> = Object.fromEntries(
  mockProjects.map((p) => [p.id, buildWorkflowEdges(p.id)])
);

export const mockDocuments: DocumentArtifact[] = [
  { id: "doc-1", projectId: "proj-loyalty", projectName: "Customer Loyalty Rewards Platform", title: "Requirement Intake Summary", artifactType: "intake_summary", status: "APPROVED", versionNumber: 2, updatedAt: "2026-08-28T09:30:00Z" },
  { id: "doc-2", projectId: "proj-loyalty", projectName: "Customer Loyalty Rewards Platform", title: "Problem Statement", artifactType: "problem_statement", status: "DRAFT", versionNumber: 1, updatedAt: "2026-08-29T08:00:00Z" },
  { id: "doc-3", projectId: "proj-vendor", projectName: "Vendor Onboarding Portal", title: "High-Level Design", artifactType: "hld_document", status: "READY_FOR_REVIEW", versionNumber: 2, updatedAt: "2026-08-29T11:15:00Z" },
  { id: "doc-4", projectId: "proj-vendor", projectName: "Vendor Onboarding Portal", title: "Solution Options", artifactType: "solution_options_doc", status: "APPROVED", versionNumber: 3, updatedAt: "2026-08-25T14:00:00Z" },
  { id: "doc-5", projectId: "proj-fraud", projectName: "Real-Time Fraud Alerts", title: "Test Report — Alerting Latency", artifactType: "test_report", status: "DRAFT", versionNumber: 1, updatedAt: "2026-08-29T16:45:00Z" },
  { id: "doc-6", projectId: "proj-fraud", projectName: "Real-Time Fraud Alerts", title: "Low-Level Design", artifactType: "lld_document", status: "APPROVED", versionNumber: 2, updatedAt: "2026-08-15T10:00:00Z" },
  { id: "doc-7", projectId: "proj-invoicing", projectName: "Automated Invoicing v2", title: "Deployment Record", artifactType: "deployment_record", status: "APPROVED", versionNumber: 1, updatedAt: "2026-07-10T12:00:00Z" },
  { id: "doc-8", projectId: "proj-legacy-crm", projectName: "Legacy CRM Data Migration", title: "Solution Options", artifactType: "solution_options_doc", status: "NEEDS_CHANGES", versionNumber: 1, updatedAt: "2026-04-02T09:00:00Z" },
];

export const mockReviews: ReviewItem[] = [
  { id: "rev-1", projectId: "proj-vendor", projectName: "Vendor Onboarding Portal", artifactId: "doc-3", artifactTitle: "High-Level Design", workflowStageName: "High-Level Design", reviewerId: "user-alex", reviewerName: "Alex Reviewer", status: "PENDING", submittedAt: "2026-08-29T11:15:00Z" },
  { id: "rev-2", projectId: "proj-loyalty", projectName: "Customer Loyalty Rewards Platform", artifactId: "doc-1", artifactTitle: "Requirement Intake Summary", workflowStageName: "Requirement Intake", reviewerId: "user-alex", reviewerName: "Alex Reviewer", status: "APPROVED", submittedAt: "2026-08-28T08:00:00Z" },
  { id: "rev-3", projectId: "proj-fraud", projectName: "Real-Time Fraud Alerts", artifactId: "doc-6", artifactTitle: "Low-Level Design", workflowStageName: "Low-Level Design", reviewerId: "user-priya", reviewerName: "Priya Dev", status: "APPROVED", submittedAt: "2026-08-14T09:00:00Z" },
  { id: "rev-4", projectId: "proj-legacy-crm", projectName: "Legacy CRM Data Migration", artifactId: "doc-8", artifactTitle: "Solution Options", workflowStageName: "Solution Discovery", reviewerId: "user-alex", reviewerName: "Alex Reviewer", status: "NEEDS_CHANGES", submittedAt: "2026-04-01T09:00:00Z" },
  { id: "rev-5", projectId: "proj-vendor", projectName: "Vendor Onboarding Portal", artifactId: "doc-4", artifactTitle: "Solution Options", workflowStageName: "Solution Discovery", reviewerId: "user-priya", reviewerName: "Priya Dev", status: "APPROVED", submittedAt: "2026-08-25T13:00:00Z" },
  { id: "rev-6", projectId: "proj-fraud", projectName: "Real-Time Fraud Alerts", artifactId: "doc-5", artifactTitle: "Test Report — Alerting Latency", workflowStageName: "Testing", reviewerId: "user-alex", reviewerName: "Alex Reviewer", status: "PENDING", submittedAt: "2026-08-29T17:00:00Z" },
];

// A standard, company-wide checklist applied to every review — not
// customized per artifact. Approval requires all boxes checked (enforced
// in the Review Detail UI); it's a process-control gate, not per-item data.
export const REVIEW_CHECKLIST_ITEMS: ReviewChecklistItem[] = [
  { id: "requirements", label: "Content fully addresses the stage's required inputs" },
  { id: "clarity", label: "Written clearly enough for the next stage to act on without re-asking questions" },
  { id: "no-sensitive-data", label: "No sensitive or confidential data is exposed inappropriately" },
  { id: "aligned", label: "Aligns with the approved output of the previous stage" },
];

export const mockAgents: AgentDefinitionSummary[] = TEMPLATE_STAGES.map((stage, i) => ({
  id: `agent-${stage.key}`,
  agentKey: stage.agentKey,
  name: `${stage.name} Agent`,
  description: `Drafts, improves, and validates the ${stage.outputArtifactType.replaceAll("_", " ")} produced by the ${stage.name} stage.`,
  modelName: "stub-no-model-configured",
  isActive: true,
  totalRuns: [12, 9, 7, 5, 4, 3, 6, 4, 2, 1, 0][i] ?? 0,
}));

// Rich, hand-authored default prompts for the 5 agents named in the Prompt
// Library spec — mirrors apps/api/app/db/seed.py's RICH_DEFAULT_PROMPTS
// exactly. Each is v2 (active), superseding a generic v1, matching the
// real backend's actual current state after seeding + enrichment.
const RICH_PROMPT_CONTENT: Record<string, { systemPrompt: string; outputFormat: string; validationChecklist: string[] }> = {
  "requirement-intake-agent": {
    systemPrompt:
      "You are the Requirement Intake agent for Agentic SDLC Hub. Given a stakeholder's raw request, produce a concise Requirement Intake Summary that captures the stakeholder, the request, any known constraints, and a measurable success metric if one is available. Do not invent constraints or metrics that weren't stated — flag them as open questions instead.",
    outputFormat: "Markdown with headings: Stakeholder & Request, Constraints, Success Metric (or Open Questions if not yet known).",
    validationChecklist: [
      "States who the stakeholder is",
      "Captures the request in one clear sentence",
      "Lists constraints or explicitly says none are known",
      "Includes a success metric or flags it as an open question",
    ],
  },
  "problem-discovery-agent": {
    systemPrompt:
      "You are the Problem Discovery agent. Given an approved Requirement Intake Summary, investigate and state the underlying problem clearly enough to design a solution against it: its impact, who it affects, and any constraints. Do not propose solutions — that's the next stage's job.",
    outputFormat: "Markdown with headings: Problem Statement, Impact, Affected Users, Constraints.",
    validationChecklist: [
      "Problem is stated as a problem, not a solution",
      "Impact is quantified or clearly qualified",
      "Affected users/systems are named",
      "Traces back to the intake summary's request",
    ],
  },
  "solution-discovery-agent": {
    systemPrompt:
      "You are the Solution Discovery agent. Given an approved Problem Statement, propose 2-3 candidate solution approaches, weigh their trade-offs, and recommend one. Be explicit about why the recommended option was chosen over the alternatives.",
    outputFormat: "Markdown with headings: Candidate Options, Trade-offs, Recommendation.",
    validationChecklist: [
      "At least two real alternatives are considered",
      "Trade-offs reference cost, risk, or timeline",
      "A single option is clearly recommended with rationale",
      "Recommendation directly addresses the problem statement",
    ],
  },
  "hld-agent": {
    systemPrompt:
      "You are the High-Level Design agent. Given the recommended solution option, define the system-level architecture: major components, how they interact, the data model at a high level, and key security considerations. Flag open questions rather than guessing at unresolved decisions.",
    outputFormat: "Markdown with headings: Overview, Architecture, Data Model, Security Considerations, Open Questions.",
    validationChecklist: [
      "Every major component has a stated responsibility",
      "Component interactions are described, not just listed",
      "Security considerations are addressed explicitly",
      "Unresolved decisions are listed as open questions, not silently assumed",
    ],
  },
  "story-crafting-agent": {
    systemPrompt:
      "You are the Story Crafting agent. Given an approved High-Level Design, break it into implementable stories with clear acceptance criteria, sized so each can reasonably be completed within one implementation pass. Do not include design details already settled in the HLD — reference them instead.",
    outputFormat: "Markdown list of stories, each with a title, description, and acceptance criteria as a checklist.",
    validationChecklist: [
      "Every story has explicit acceptance criteria",
      "Stories are independently completable",
      "No story silently re-decides something already settled in the HLD",
      "Together, the stories cover the full HLD scope",
    ],
  },
};

export const mockPromptVersionsByAgent: Record<string, AgentPromptVersion[]> = Object.fromEntries(
  TEMPLATE_STAGES.map((stage) => {
    const rich = RICH_PROMPT_CONTENT[stage.agentKey];
    const genericV1: AgentPromptVersion = {
      id: `${stage.agentKey}-prompt-v1`,
      agentKey: stage.agentKey,
      role: "draft",
      name: `${stage.name} Agent`,
      stage: stage.key,
      systemPrompt: `You are the ${stage.name} agent. Given the following inputs: ${stage.requiredInputs.join(", ") || "none"}, draft a ${stage.outputArtifactType}. ${stage.description}`,
      outputFormat: "Markdown document.",
      validationChecklist: rich ? [] : ["Addresses all of the stage's required inputs", "Uses valid Markdown formatting"],
      version: 1,
      isActive: !rich,
      createdAt: "2026-08-27T09:00:00Z",
      updatedAt: "2026-08-27T09:00:00Z",
    };

    if (!rich) return [stage.agentKey, [genericV1]];

    const richV2: AgentPromptVersion = {
      id: `${stage.agentKey}-prompt-v2`,
      agentKey: stage.agentKey,
      role: "draft",
      name: `${stage.name} — Draft Prompt`,
      stage: stage.key,
      systemPrompt: rich.systemPrompt,
      outputFormat: rich.outputFormat,
      validationChecklist: rich.validationChecklist,
      version: 2,
      isActive: true,
      createdAt: "2026-08-29T20:13:00Z",
      updatedAt: "2026-08-29T20:13:00Z",
    };
    return [stage.agentKey, [genericV1, richV2]];
  })
);

export function getActivePrompt(agentKey: string): AgentPromptVersion | undefined {
  return mockPromptVersionsByAgent[agentKey]?.find((p) => p.isActive);
}

export const mockAgentRuns: AgentRunSummary[] = [
  { id: "run-1", projectId: "proj-loyalty", projectName: "Customer Loyalty Rewards Platform", agentName: "Problem Discovery Agent", workflowStageName: "Problem Discovery", action: "draft", status: "COMPLETED", createdAt: "2026-08-29T08:00:00Z" },
  { id: "run-2", projectId: "proj-fraud", projectName: "Real-Time Fraud Alerts", agentName: "Testing Agent", workflowStageName: "Testing", action: "draft", status: "RUNNING", createdAt: "2026-08-29T16:40:00Z" },
  { id: "run-3", projectId: "proj-vendor", projectName: "Vendor Onboarding Portal", agentName: "High-Level Design Agent", workflowStageName: "High-Level Design", action: "improve", status: "COMPLETED", createdAt: "2026-08-29T10:50:00Z" },
  { id: "run-4", projectId: "proj-vendor", projectName: "Vendor Onboarding Portal", agentName: "Solution Discovery Agent", workflowStageName: "Solution Discovery", action: "validate", status: "COMPLETED", createdAt: "2026-08-25T13:30:00Z" },
  { id: "run-5", projectId: "proj-legacy-crm", projectName: "Legacy CRM Data Migration", agentName: "Solution Discovery Agent", workflowStageName: "Solution Discovery", action: "draft", status: "FAILED", createdAt: "2026-04-01T09:30:00Z" },
  { id: "run-6", projectId: "proj-loyalty", projectName: "Customer Loyalty Rewards Platform", agentName: "Requirement Intake Agent", workflowStageName: "Requirement Intake", action: "draft", status: "COMPLETED", createdAt: "2026-08-27T10:30:00Z" },
];

/** The 11 stage keys, in workflow order — for anything that needs to walk stages in sequence. */
export const STAGE_ORDER = TEMPLATE_STAGES.map((s) => s.key);

export function getStageLabel(nodeKey: string): string {
  return TEMPLATE_STAGES.find((s) => s.key === nodeKey)?.name ?? nodeKey;
}

/** Number of projects with at least one workflow node stuck in BLOCKED. */
export function getBlockedProjectCount(): number {
  return mockProjects.filter((p) => (mockWorkflowNodesByProject[p.id] ?? []).some((n) => n.status === "BLOCKED")).length;
}

export function getProjectById(id: string): Project | undefined {
  return mockProjects.find((p) => p.id === id);
}

