import Link from "next/link";
import { Plus } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectsTable } from "@/components/projects/projects-table";
import { buttonVariants } from "@/components/ui/button";
import { mockProjects } from "@/lib/mock-data";

export default function ProjectsPage() {
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
      <ProjectsTable projects={mockProjects} />
    </div>
  );
}
