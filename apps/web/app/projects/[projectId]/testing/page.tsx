import { Suspense } from "react";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { TestingView } from "@/components/testing/testing-view";
import { api, ApiError } from "@/lib/api";
import { toImplementationTask, toProject } from "@/lib/mappers";

export default async function TestingPage({ params }: { params: { projectId: string } }) {
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

  // Eligible = has a latest implementation run that was ACCEPTED — the
  // real gate (see app/models/test_run.py); a PR/diff check happens
  // server-side when a run is actually started.
  const runsByTask = await Promise.all(
    tasks.map((t) => api.projects.implementationTaskRuns(project.id, t.id))
  );
  const eligibleTasks = tasks.filter((_, i) => runsByTask[i].some((r) => r.review_status === "ACCEPTED"));

  const currentUserId = users[0]?.id ?? null;
  const reviewers = users.filter((u) => u.role === "QA" || u.role === "ADMIN").map((u) => ({ id: u.id, name: u.full_name }));

  return (
    <div>
      <PageHeader title={project.name} description="Testing Agent runs and QA review for accepted implementation changes." />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>
      <TestingView eligibleTasks={eligibleTasks} currentUserId={currentUserId} reviewers={reviewers.length > 0 ? reviewers : users.map((u) => ({ id: u.id, name: u.full_name }))} />
    </div>
  );
}
