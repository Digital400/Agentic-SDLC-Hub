import { Suspense } from "react";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { SprintPlanningView } from "@/components/sprints/sprint-planning-view";
import { api, ApiError } from "@/lib/api";
import { toProject } from "@/lib/mappers";

export default async function SprintPlanningPage({ params }: { params: { projectId: string } }) {
  let project;
  try {
    project = toProject(await api.projects.get(params.projectId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [sprints, storiesResponse, users] = await Promise.all([
    api.sprints.list(project.id),
    api.stories.list(project.id),
    api.users.list(),
  ]);
  const currentUserId = users[0]?.id ?? null;

  return (
    <div>
      <PageHeader
        title={project.name}
        description="Plan a sprint: set its goal, dates, and capacity, pull in approved stories, assign owners, and start it when ready."
      />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>
      <SprintPlanningView
        projectId={project.id}
        initialSprints={sprints}
        initialStories={storiesResponse.items}
        users={users}
        currentUserId={currentUserId}
      />
    </div>
  );
}
