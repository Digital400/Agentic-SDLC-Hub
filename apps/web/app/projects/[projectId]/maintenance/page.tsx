import { Suspense } from "react";
import { notFound } from "next/navigation";

import { MaintenanceView } from "@/components/maintenance/maintenance-view";
import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { api, ApiError } from "@/lib/api";
import { toMaintenanceRun, toProject } from "@/lib/mappers";

export default async function MaintenancePage({ params }: { params: { projectId: string } }) {
  let project;
  try {
    project = toProject(await api.projects.get(params.projectId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [apiRuns, users] = await Promise.all([
    api.projects.maintenanceRuns(project.id),
    api.users.list(),
  ]);
  const runs = apiRuns.map(toMaintenanceRun);
  const currentUserId = users[0]?.id ?? null;

  return (
    <div>
      <PageHeader
        title={project.name}
        description="Maintenance Agent — post-release health reports. Generate one manually any time (e.g. weekly); it only recommends actions and never changes production."
      />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>
      <MaintenanceView projectId={project.id} initialRuns={runs} currentUserId={currentUserId} />
    </div>
  );
}
