import { Suspense } from "react";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { WorkflowCanvas } from "@/components/workflow/workflow-canvas";
import { api, ApiError } from "@/lib/api";
import { toDocumentArtifact, toProject, toReviewItem, toWorkflowEdge, toWorkflowNode } from "@/lib/mappers";

export default async function ProjectWorkflowPage({ params }: { params: { projectId: string } }) {
  let project;
  try {
    project = toProject(await api.projects.get(params.projectId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [apiNodes, apiEdges, apiArtifacts, apiReviews, users] = await Promise.all([
    api.projects.workflowNodes(project.id),
    api.projects.workflowEdges(project.id),
    api.projects.artifacts(project.id),
    api.reviews.listAll(),
    api.users.list(),
  ]);

  const nodes = apiNodes.map(toWorkflowNode);
  const edges = apiEdges.map(toWorkflowEdge);
  const documents = apiArtifacts.map(toDocumentArtifact);
  const reviews = apiReviews.filter((r) => r.project_id === project.id).map(toReviewItem);
  const currentUserId = users[0]?.id ?? null;

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
        currentUserId={currentUserId}
      />
    </div>
  );
}
