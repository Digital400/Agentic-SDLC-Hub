import { Suspense } from "react";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { PRReviewView } from "@/components/pr-review/pr-review-view";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { api, ApiError } from "@/lib/api";
import { toImplementationTask, toProject } from "@/lib/mappers";

export default async function PRReviewPage({ params }: { params: { projectId: string } }) {
  let project;
  try {
    project = toProject(await api.projects.get(params.projectId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [apiTasks, users] = await Promise.all([
    api.projects.implementationTasks(project.id),
    api.users.list(),
  ]);
  const tasks = apiTasks.map(toImplementationTask);

  // Eligible = an ACCEPTED implementation run with a created PR — this
  // feature's own gate (see app/models/pr_review_run.py's class
  // docstring), stricter than Testing's PR-or-diff either/or.
  const runsByTask = await Promise.all(tasks.map((t) => api.projects.implementationTaskRuns(project.id, t.id)));
  const eligibleTasks = tasks.filter((_, i) =>
    runsByTask[i].some((r) => r.review_status === "ACCEPTED" && r.pull_request !== null)
  );

  const currentUserId = users[0]?.id ?? null;

  return (
    <div>
      <PageHeader title={project.name} description="PR Review Agent runs — advisory findings for the human reviewer on GitHub." />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>
      <PRReviewView eligibleTasks={eligibleTasks} currentUserId={currentUserId} />
    </div>
  );
}
