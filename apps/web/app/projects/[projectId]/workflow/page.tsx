import { Suspense } from "react";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { WorkflowCanvas } from "@/components/workflow/workflow-canvas";
import { api, ApiError } from "@/lib/api";
import {
  toAgentRunDetail,
  toDocumentArtifact,
  toProject,
  toReviewItem,
  toValidatorDefinition,
  toWorkflowEdge,
  toWorkflowNode,
} from "@/lib/mappers";

export default async function ProjectWorkflowPage({ params }: { params: { projectId: string } }) {
  let project;
  try {
    project = toProject(await api.projects.get(params.projectId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [apiNodes, apiEdges, apiArtifacts, apiReviews, users, apiAgentRuns, apiValidators] = await Promise.all([
    api.projects.workflowNodes(project.id),
    api.projects.workflowEdges(project.id),
    api.projects.artifacts(project.id),
    api.reviews.listAll(),
    api.users.list(),
    api.projects.agentRuns(project.id),
    api.validators.list(),
  ]);

  const nodes = apiNodes.map(toWorkflowNode);
  const edges = apiEdges.map(toWorkflowEdge);
  const documents = apiArtifacts.map(toDocumentArtifact);
  const reviews = apiReviews.filter((r) => r.project_id === project.id).map(toReviewItem);
  // No login exists yet (see app/layout.tsx) — the same default-first-user
  // convention is reused for "who is acting", so admin-gating here checks
  // that same user's real role rather than a separate auth concept.
  const currentUserId = users[0]?.id ?? null;
  const isAdmin = users[0]?.role === "ADMIN";

  const nodeNameById = new Map(nodes.map((n) => [n.id, n.name]));
  const agentRuns = apiAgentRuns.map((r) => toAgentRunDetail(r, project.name, nodeNameById.get(r.workflow_node_id) ?? ""));
  const validators = apiValidators.map(toValidatorDefinition);

  return (
    <div>
      <PageHeader title={project.name} description="Workflow graph — click a stage to see its details." />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>
      <WorkflowCanvas
        projectId={project.id}
        nodes={nodes}
        edges={edges}
        documents={documents}
        reviews={reviews}
        agentRuns={agentRuns}
        validators={validators}
        currentUserId={currentUserId}
        isAdmin={isAdmin}
      />
    </div>
  );
}
