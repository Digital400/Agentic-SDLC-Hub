import { Suspense } from "react";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { StoryLaneWorkspace } from "@/components/stories/story-lane-workspace";
import { api, ApiError } from "@/lib/api";
import { toProject } from "@/lib/mappers";

export default async function StoryLanePage({ params }: { params: { projectId: string; storyId: string } }) {
  let project;
  try {
    project = toProject(await api.projects.get(params.projectId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [storiesResponse, users] = await Promise.all([api.stories.list(project.id), api.users.list()]);
  const story = storiesResponse.items.find((s) => s.id === params.storyId);
  if (!story) notFound();

  let lane;
  try {
    lane = await api.stories.getLane(story.id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound(); // no lane created yet for this story
    throw err;
  }

  const [nodes, lld, implementationPlan, testScenarios, implementationTask] = await Promise.all([
    api.storyDelivery.listNodes(lane.id),
    api.stories.getLld(story.id).catch((err) => {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }),
    api.stories.getImplementationPlan(story.id).catch((err) => {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }),
    api.stories.getTestScenarios(story.id).catch((err) => {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }),
    api.stories.getImplementationTask(story.id).catch((err) => {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }),
  ]);

  const [implementationRuns, testRuns, prReviewRuns, testReport, testExecutions] = await Promise.all([
    implementationTask ? api.projects.implementationTaskRuns(project.id, implementationTask.id) : Promise.resolve([]),
    implementationTask ? api.projects.implementationTaskTestRuns(project.id, implementationTask.id) : Promise.resolve([]),
    implementationTask ? api.projects.implementationTaskPrReviewRuns(project.id, implementationTask.id) : Promise.resolve([]),
    api.stories.getTestReport(story.id).catch((err) => {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }),
    api.storyTestExecutions.listForStory(story.id).catch(() => []),
  ]);

  const currentUserId = users[0]?.id ?? null;
  const confluenceSpace = await api.projects.confluenceSpace(project.id).catch(() => null);
  const confluencePreview = confluenceSpace ? await api.confluence.storyPublishPreview(story.id).catch(() => null) : null;
  const initialConfluenceItem = confluencePreview?.items.find((i) => i.artifact_type === "story_lld") ?? null;

  return (
    <div>
      <PageHeader
        title={`${project.name} — ${story.title}`}
        description="This story's own delivery lane — runs in parallel with every other story's lane."
      />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>
      <StoryLaneWorkspace
        projectId={project.id}
        story={story}
        initialLane={lane}
        initialNodes={nodes}
        initialLld={lld}
        initialImplementationPlan={implementationPlan}
        initialTestScenarios={testScenarios}
        initialImplementationTask={implementationTask}
        initialImplementationRuns={implementationRuns}
        initialTestRuns={testRuns}
        initialPrReviewRuns={prReviewRuns}
        initialTestReport={testReport}
        initialTestExecutions={testExecutions}
        confluenceConnected={confluenceSpace !== null}
        initialConfluenceItem={initialConfluenceItem}
        users={users}
        currentUserId={currentUserId}
      />
    </div>
  );
}
