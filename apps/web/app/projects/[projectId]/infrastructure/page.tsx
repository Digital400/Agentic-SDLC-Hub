import { Suspense } from "react";
import { notFound } from "next/navigation";

import { InfrastructurePlanningView } from "@/components/infrastructure/infrastructure-planning-view";
import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { api, ApiError } from "@/lib/api";
import {
  toAgentRunDetail,
  toDocumentArtifact,
  toProject,
  toReviewItem,
  toValidatorDefinition,
  toWorkflowNode,
} from "@/lib/mappers";

export default async function InfrastructurePlanningPage({ params }: { params: { projectId: string } }) {
  let project;
  try {
    project = toProject(await api.projects.get(params.projectId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [apiNodes, apiArtifacts, apiReviews, users, apiAgentRuns, apiValidators] = await Promise.all([
    api.projects.workflowNodes(project.id),
    api.projects.artifacts(project.id),
    api.reviews.listAll(),
    api.users.list(),
    api.projects.agentRuns(project.id),
    api.validators.list(),
  ]);

  const nodes = apiNodes.map(toWorkflowNode);
  // Projects created before this stage existed won't have this node — see
  // apps/api/app/services/workflow_templates.py's generate_workflow_graph
  // docstring: the template only materializes onto a project once, at
  // creation time.
  const node = nodes.find((n) => n.nodeKey === "infrastructure_planning") ?? null;

  const documents = apiArtifacts.map(toDocumentArtifact);
  const reviews = apiReviews.filter((r) => r.project_id === project.id).map(toReviewItem);
  const knownArtifactTypes = new Set(nodes.map((n) => n.outputArtifactType));
  const freeformInputKeys = node ? node.requiredInputs.filter((input) => !knownArtifactTypes.has(input)) : [];

  const nodeNameById = new Map(nodes.map((n) => [n.id, n.name]));
  const agentRuns = apiAgentRuns.map((r) => toAgentRunDetail(r, project.name, nodeNameById.get(r.workflow_node_id) ?? ""));
  const lastRun = node ? agentRuns.filter((r) => r.workflowNodeId === node.id).sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))[0] ?? null : null;

  const validators = apiValidators.map(toValidatorDefinition);
  const validator = node ? validators.find((v) => v.stage === node.nodeKey) ?? null : null;

  const currentUserId = users[0]?.id ?? null;
  const isAdmin = users[0]?.role === "ADMIN";

  return (
    <div>
      <PageHeader
        title={project.name}
        description="Infrastructure Planning — environments, CI/CD, secrets, monitoring, security, rollback, and cost, planned from the approved HLD and LLD. Requires DevOps approval; nothing here deploys automatically."
      />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>
      <InfrastructurePlanningView
        projectId={project.id}
        node={node}
        documents={documents}
        reviews={reviews}
        freeformInputKeys={freeformInputKeys}
        lastRun={lastRun}
        validator={validator}
        currentUserId={currentUserId}
        isAdmin={isAdmin}
      />
    </div>
  );
}
