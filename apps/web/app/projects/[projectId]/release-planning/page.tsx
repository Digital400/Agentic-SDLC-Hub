import { Suspense } from "react";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { ReleasePlanningView } from "@/components/releases/release-planning-view";
import { api, ApiError } from "@/lib/api";
import { toProject } from "@/lib/mappers";

export default async function ReleasePlanningPage({ params }: { params: { projectId: string } }) {
  let project;
  try {
    project = toProject(await api.projects.get(params.projectId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [releases, users] = await Promise.all([api.releases.list(project.id), api.users.list()]);
  const currentUserId = users[0]?.id ?? null;

  return (
    <div>
      <PageHeader
        title={project.name}
        description="Curate a release from any story whose delivery lane has reached Release Ready, generate its release notes, and approve it."
      />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>
      <ReleasePlanningView projectId={project.id} initialReleases={releases} currentUserId={currentUserId} />
    </div>
  );
}
