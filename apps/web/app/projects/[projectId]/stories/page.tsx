import { Suspense } from "react";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { StoriesView } from "@/components/stories/stories-view";
import { api, ApiError } from "@/lib/api";
import { toProject } from "@/lib/mappers";

export default async function StoriesPage({ params }: { params: { projectId: string } }) {
  let project;
  try {
    project = toProject(await api.projects.get(params.projectId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [nodes, storiesResponse, sprints, users, jiraProject] = await Promise.all([
    api.projects.workflowNodes(project.id),
    api.stories.list(project.id),
    api.sprints.list(project.id),
    api.users.list(),
    api.projects.jiraProject(project.id).catch(() => null),
  ]);
  const storyCraftingNode = nodes.find((n) => n.node_key === "story_crafting");
  const storyCraftingApproved = storyCraftingNode?.status === "APPROVED";
  const currentUserId = users[0]?.id ?? null;

  return (
    <div>
      <PageHeader
        title={project.name}
        description="Stories synced from the approved Story Crafting backlog — assign an owner, sync to Jira, and create each story's own parallel delivery lane."
      />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>
      <StoriesView
        projectId={project.id}
        storyCraftingApproved={storyCraftingApproved}
        initialStories={storiesResponse.items}
        initialSprints={sprints}
        users={users}
        currentUserId={currentUserId}
        jiraConnected={jiraProject !== null}
      />
    </div>
  );
}
