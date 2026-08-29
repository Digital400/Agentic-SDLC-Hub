import type { WorkflowStatus } from "@agentic-sdlc-hub/shared";
import type {
  AgentDefinitionSummary,
  AgentRunSummary,
  DocumentArtifact,
  KnowledgeBaseSource,
  Project,
  ProjectWorkflowEdge,
  ProjectWorkflowNode,
  ReviewItem,
} from "@/lib/types";

const FULL_REVIEW_ACTIONS = ["draft", "edit", "submit_for_review", "approve", "reject", "request_changes"];
const NO_REVIEW_ACTIONS = ["draft", "edit", "submit_for_review", "complete"];

// Mirrors workflows/sdlc-workflow.json's 11 stages exactly (name,
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
  { key: "testing", name: "Testing", description: "Validate the implementation against acceptance criteria and produce a test report.", agentKey: "testing-agent", assignedRole: "QA Engineer", requiredInputs: ["code_change"], outputArtifactType: "test_report", requiresHumanApproval: true, allowedActions: FULL_REVIEW_ACTIONS, x: 1540 },
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
    status: statuses[i] ?? "NOT_STARTED",
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
    workflowTemplateId: "default-sdlc-workflow",
    createdAt: "2026-03-15T09:00:00Z",
    updatedAt: "2026-04-02T09:00:00Z",
  },
];

export const mockWorkflowNodesByProject: Record<string, ProjectWorkflowNode[]> = {
  "proj-loyalty": buildWorkflowNodes("proj-loyalty", [
    "COMPLETED", "IN_PROGRESS", "NOT_STARTED", "NOT_STARTED", "NOT_STARTED",
    "NOT_STARTED", "NOT_STARTED", "NOT_STARTED", "NOT_STARTED", "NOT_STARTED", "NOT_STARTED",
  ]),
  "proj-vendor": buildWorkflowNodes("proj-vendor", [
    "COMPLETED", "COMPLETED", "COMPLETED", "WAITING_FOR_REVIEW", "NOT_STARTED",
    "NOT_STARTED", "NOT_STARTED", "NOT_STARTED", "NOT_STARTED", "NOT_STARTED", "NOT_STARTED",
  ]),
  "proj-fraud": buildWorkflowNodes("proj-fraud", [
    "COMPLETED", "COMPLETED", "COMPLETED", "COMPLETED", "COMPLETED",
    "COMPLETED", "COMPLETED", "IN_PROGRESS", "NOT_STARTED", "NOT_STARTED", "NOT_STARTED",
  ]),
  "proj-invoicing": buildWorkflowNodes("proj-invoicing", Array(11).fill("COMPLETED") as WorkflowStatus[]),
  "proj-legacy-crm": buildWorkflowNodes("proj-legacy-crm", [
    "COMPLETED", "COMPLETED", "BLOCKED", "NOT_STARTED", "NOT_STARTED",
    "NOT_STARTED", "NOT_STARTED", "NOT_STARTED", "NOT_STARTED", "NOT_STARTED", "NOT_STARTED",
  ]),
};

export const mockWorkflowEdgesByProject: Record<string, ProjectWorkflowEdge[]> = Object.fromEntries(
  mockProjects.map((p) => [p.id, buildWorkflowEdges(p.id)])
);

export const mockDocuments: DocumentArtifact[] = [
  { id: "doc-1", projectId: "proj-loyalty", projectName: "Customer Loyalty Rewards Platform", title: "Requirement Intake Summary", artifactType: "intake_summary", status: "APPROVED", versionNumber: 2, updatedAt: "2026-08-28T09:30:00Z" },
  { id: "doc-2", projectId: "proj-loyalty", projectName: "Customer Loyalty Rewards Platform", title: "Problem Statement", artifactType: "problem_statement", status: "IN_PROGRESS", versionNumber: 1, updatedAt: "2026-08-29T08:00:00Z" },
  { id: "doc-3", projectId: "proj-vendor", projectName: "Vendor Onboarding Portal", title: "High-Level Design", artifactType: "hld_document", status: "WAITING_FOR_REVIEW", versionNumber: 1, updatedAt: "2026-08-29T11:15:00Z" },
  { id: "doc-4", projectId: "proj-vendor", projectName: "Vendor Onboarding Portal", title: "Solution Options", artifactType: "solution_options_doc", status: "APPROVED", versionNumber: 3, updatedAt: "2026-08-25T14:00:00Z" },
  { id: "doc-5", projectId: "proj-fraud", projectName: "Real-Time Fraud Alerts", title: "Test Report — Alerting Latency", artifactType: "test_report", status: "IN_PROGRESS", versionNumber: 1, updatedAt: "2026-08-29T16:45:00Z" },
  { id: "doc-6", projectId: "proj-fraud", projectName: "Real-Time Fraud Alerts", title: "Low-Level Design", artifactType: "lld_document", status: "APPROVED", versionNumber: 2, updatedAt: "2026-08-15T10:00:00Z" },
  { id: "doc-7", projectId: "proj-invoicing", projectName: "Automated Invoicing v2", title: "Deployment Record", artifactType: "deployment_record", status: "COMPLETED", versionNumber: 1, updatedAt: "2026-07-10T12:00:00Z" },
  { id: "doc-8", projectId: "proj-legacy-crm", projectName: "Legacy CRM Data Migration", title: "Solution Options", artifactType: "solution_options_doc", status: "BLOCKED", versionNumber: 1, updatedAt: "2026-04-02T09:00:00Z" },
];

export const mockReviews: ReviewItem[] = [
  { id: "rev-1", projectId: "proj-vendor", projectName: "Vendor Onboarding Portal", artifactTitle: "High-Level Design", workflowStageName: "High-Level Design", reviewerName: "Alex Reviewer", status: "PENDING", submittedAt: "2026-08-29T11:15:00Z" },
  { id: "rev-2", projectId: "proj-loyalty", projectName: "Customer Loyalty Rewards Platform", artifactTitle: "Requirement Intake Summary", workflowStageName: "Requirement Intake", reviewerName: "Alex Reviewer", status: "APPROVED", submittedAt: "2026-08-28T08:00:00Z" },
  { id: "rev-3", projectId: "proj-fraud", projectName: "Real-Time Fraud Alerts", artifactTitle: "Low-Level Design", workflowStageName: "Low-Level Design", reviewerName: "Priya Dev", status: "APPROVED", submittedAt: "2026-08-14T09:00:00Z" },
  { id: "rev-4", projectId: "proj-legacy-crm", projectName: "Legacy CRM Data Migration", artifactTitle: "Solution Options", workflowStageName: "Solution Discovery", reviewerName: "Alex Reviewer", status: "NEEDS_CHANGES", submittedAt: "2026-04-01T09:00:00Z" },
  { id: "rev-5", projectId: "proj-vendor", projectName: "Vendor Onboarding Portal", artifactTitle: "Solution Options", workflowStageName: "Solution Discovery", reviewerName: "Priya Dev", status: "APPROVED", submittedAt: "2026-08-25T13:00:00Z" },
  { id: "rev-6", projectId: "proj-fraud", projectName: "Real-Time Fraud Alerts", artifactTitle: "Test Report — Alerting Latency", workflowStageName: "Testing", reviewerName: "Alex Reviewer", status: "PENDING", submittedAt: "2026-08-29T17:00:00Z" },
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

export const mockAgentRuns: AgentRunSummary[] = [
  { id: "run-1", projectId: "proj-loyalty", projectName: "Customer Loyalty Rewards Platform", agentName: "Problem Discovery Agent", workflowStageName: "Problem Discovery", action: "draft", status: "COMPLETED", createdAt: "2026-08-29T08:00:00Z" },
  { id: "run-2", projectId: "proj-fraud", projectName: "Real-Time Fraud Alerts", agentName: "Testing Agent", workflowStageName: "Testing", action: "draft", status: "RUNNING", createdAt: "2026-08-29T16:40:00Z" },
  { id: "run-3", projectId: "proj-vendor", projectName: "Vendor Onboarding Portal", agentName: "High-Level Design Agent", workflowStageName: "High-Level Design", action: "improve", status: "COMPLETED", createdAt: "2026-08-29T10:50:00Z" },
  { id: "run-4", projectId: "proj-vendor", projectName: "Vendor Onboarding Portal", agentName: "Solution Discovery Agent", workflowStageName: "Solution Discovery", action: "validate", status: "COMPLETED", createdAt: "2026-08-25T13:30:00Z" },
  { id: "run-5", projectId: "proj-legacy-crm", projectName: "Legacy CRM Data Migration", agentName: "Solution Discovery Agent", workflowStageName: "Solution Discovery", action: "draft", status: "FAILED", createdAt: "2026-04-01T09:30:00Z" },
  { id: "run-6", projectId: "proj-loyalty", projectName: "Customer Loyalty Rewards Platform", agentName: "Requirement Intake Agent", workflowStageName: "Requirement Intake", action: "draft", status: "COMPLETED", createdAt: "2026-08-27T10:30:00Z" },
];

export const mockKnowledgeBase: KnowledgeBaseSource[] = [
  { id: "kb-1", title: "Requirement Intake Summary — Customer Loyalty Rewards Platform", sourceType: "Project artifact", projectName: "Customer Loyalty Rewards Platform", indexed: true, updatedAt: "2026-08-28T09:30:00Z" },
  { id: "kb-2", title: "High-Level Design — Vendor Onboarding Portal", sourceType: "Project artifact", projectName: "Vendor Onboarding Portal", indexed: false, updatedAt: "2026-08-29T11:15:00Z" },
  { id: "kb-3", title: "Company SDLC Style Guide", sourceType: "Uploaded document", indexed: true, updatedAt: "2026-06-01T09:00:00Z" },
  { id: "kb-4", title: "Data Retention Policy (Legal wiki)", sourceType: "External link", indexed: false, updatedAt: "2026-05-12T09:00:00Z" },
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
