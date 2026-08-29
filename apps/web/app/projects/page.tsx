import Link from "next/link";
import { Plus } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectsTable } from "@/components/projects/projects-table";
import { buttonVariants } from "@/components/ui/button";
import { api } from "@/lib/api";
import { toProject } from "@/lib/mappers";

export default async function ProjectsPage() {
  const { items } = await api.projects.list();
  const projects = items.map(toProject);

  return (
    <div>
      <PageHeader
        title="Projects"
        description="Every project running through the SDLC workflow."
        actions={
          <Link href="/projects/new" className={buttonVariants()}>
            <Plus className="h-4 w-4" />
            New project
          </Link>
        }
      />
      <ProjectsTable projects={projects} />
    </div>
  );
}
