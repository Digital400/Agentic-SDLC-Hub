import { PageHeader } from "@/components/layout/page-header";
import { CreateProjectWizard } from "@/components/projects/create-project-wizard";
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
        description="Configure engineering setup — technology stack, GitHub/Jira, coding standards, guardrails, documentation, and build/test commands — before implementation agents can run."
      />
      <CreateProjectWizard createdById={defaultUserId} />
    </div>
  );
}
