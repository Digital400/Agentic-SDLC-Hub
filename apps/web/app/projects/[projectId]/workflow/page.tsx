import { Suspense } from "react";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { WorkflowCanvas } from "@/components/workflow/workflow-canvas";
import { getProjectById, mockWorkflowEdgesByProject, mockWorkflowNodesByProject } from "@/lib/mock-data";

export default function ProjectWorkflowPage({ params }: { params: { projectId: string } }) {
  const project = getProjectById(params.projectId);
  if (!project) notFound();

  const nodes = mockWorkflowNodesByProject[project.id] ?? [];
  const edges = mockWorkflowEdgesByProject[project.id] ?? [];

  return (
    <div>
      <PageHeader title={project.name} description="Workflow graph — click a stage to see its details." />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>
      <WorkflowCanvas projectId={project.id} nodes={nodes} edges={edges} />
    </div>
  );
}
