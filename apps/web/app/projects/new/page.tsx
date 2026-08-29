import { PageHeader } from "@/components/layout/page-header";
import { CreateProjectForm } from "@/components/projects/create-project-form";

export default function NewProjectPage() {
  return (
    <div>
      <PageHeader
        title="Create project"
        description="Starts the project on the default SDLC workflow, at Requirement Intake."
      />
      <CreateProjectForm />
    </div>
  );
}
