import { PageHeader } from "@/components/layout/page-header";
import { CreateProjectForm } from "@/components/projects/create-project-form";
import { api } from "@/lib/api";

export default async function NewProjectPage() {
  // No auth/login yet (see docs/mvp-plan.md), so there's no signed-in user
  // to attribute the project to — default to the first seeded user, same
  // convention used everywhere else "created by" is needed without auth.
  const users = await api.users.list();
  const defaultUserId = users[0]?.id ?? null;

  return (
    <div>
      <PageHeader
        title="Create project"
        description="Starts the project on the default SDLC workflow, at Requirement Intake."
      />
      <CreateProjectForm createdById={defaultUserId} />
    </div>
  );
}
