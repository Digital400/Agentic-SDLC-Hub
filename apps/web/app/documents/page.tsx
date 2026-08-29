import { Suspense } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { DocumentsTable } from "@/components/documents/documents-table";
import { api } from "@/lib/api";
import { toDocumentArtifact } from "@/lib/mappers";

export default async function DocumentsPage() {
  // No global "list all artifacts" endpoint exists yet — aggregate per
  // project, same tradeoff noted on the dashboard page.
  const { items: projects } = await api.projects.list();
  const perProjectArtifacts = await Promise.all(projects.map((p) => api.projects.artifacts(p.id)));
  const documents = perProjectArtifacts.flat().map(toDocumentArtifact);

  return (
    <div>
      <PageHeader title="Documents" description="Every artifact produced across all projects." />
      <Suspense fallback={null}>
        <DocumentsTable documents={documents} />
      </Suspense>
    </div>
  );
}
