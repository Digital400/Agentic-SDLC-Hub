import { Suspense } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { DocumentsTable } from "@/components/documents/documents-table";
import { mockDocuments } from "@/lib/mock-data";

export default function DocumentsPage() {
  return (
    <div>
      <PageHeader title="Documents" description="Every artifact produced across all projects." />
      <Suspense fallback={null}>
        <DocumentsTable documents={mockDocuments} />
      </Suspense>
    </div>
  );
}
