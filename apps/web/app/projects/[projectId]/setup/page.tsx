import { Suspense } from "react";
import { notFound } from "next/navigation";

import { EngineeringSetupView } from "@/components/projects/engineering-setup-view";
import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { api, ApiError } from "@/lib/api";
import { toProject } from "@/lib/mappers";

export default async function EngineeringSetupPage({ params }: { params: { projectId: string } }) {
  let project;
  try {
    project = toProject(await api.projects.get(params.projectId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [setup, users] = await Promise.all([api.engineeringSetup.get(project.id), api.users.list()]);
  const currentUserId = users[0]?.id ?? null;

  return (
    <div>
      <PageHeader
        title={project.name}
        description="Engineering Setup — the technology stack, GitHub/Jira setup, coding standards, guardrails, documentation flow, and build/test commands captured when this project was created. Edit any section below; changes apply immediately and are used by every agent run from then on."
      />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>
      <EngineeringSetupView projectId={project.id} initialSetup={setup} currentUserId={currentUserId} />
    </div>
  );
}
