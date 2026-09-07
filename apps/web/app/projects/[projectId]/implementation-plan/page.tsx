import { Suspense } from "react";
import { notFound } from "next/navigation";

import { ImplementationPlanView } from "@/components/implementation-plan/implementation-plan-view";
import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { api, ApiError } from "@/lib/api";
import { toGithubRepositoryItem, toImplementationTask, toProject, toWorkflowNode } from "@/lib/mappers";

export default async function ImplementationPlanPage({ params }: { params: { projectId: string } }) {
  let project;
  try {
    project = toProject(await api.projects.get(params.projectId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [apiTasks, apiNodes, apiReviews, users, apiRepositories] = await Promise.all([
    api.projects.implementationTasks(project.id),
    api.projects.workflowNodes(project.id),
    api.reviews.listAll(),
    api.users.list(),
    api.projects.githubRepositories(project.id),
  ]);

  const repositories = apiRepositories.map(toGithubRepositoryItem);
  const nodes = apiNodes.map(toWorkflowNode);
  const node = nodes.find((n) => n.nodeKey === "implementation_planning") ?? null;
  const tasks = apiTasks.map(toImplementationTask);

  // The plan's own pending review, if one is currently open — "Approve
  // Implementation Plan" is the existing review-decision screen at
  // /reviews/{id}, not a separate action on this page.
  const pendingReview = node
    ? apiReviews.find((r) => r.workflow_node_id === node.id && r.status === "PENDING")
    : undefined;

  const currentUserId = users[0]?.id ?? null;
  const reviewers = users.map((u) => ({ id: u.id, name: u.full_name }));

  return (
    <div>
      <PageHeader title={project.name} description="The structured task breakdown generated from the approved LLD." />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>
      <ImplementationPlanView
        projectId={project.id}
        node={node}
        tasks={tasks}
        pendingReviewId={pendingReview?.id ?? null}
        currentUserId={currentUserId}
        reviewers={reviewers}
        hasRepository={repositories.length > 0}
        repositories={repositories}
      />
    </div>
  );
}
