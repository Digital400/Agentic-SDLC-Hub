import Link from "next/link";
import { Suspense } from "react";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { ProjectStatusBadge, WorkflowStatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDate } from "@/lib/format";
import { getProjectById, mockDocuments, mockReviews, mockWorkflowNodesByProject } from "@/lib/mock-data";

export default function ProjectWorkspacePage({ params }: { params: { projectId: string } }) {
  const project = getProjectById(params.projectId);
  if (!project) notFound();

  const nodes = mockWorkflowNodesByProject[project.id] ?? [];
  const completedCount = nodes.filter((n) => n.status === "COMPLETED").length;
  const progressPct = nodes.length > 0 ? Math.round((completedCount / nodes.length) * 100) : 0;

  const documents = mockDocuments.filter((d) => d.projectId === project.id).slice(0, 4);
  const reviews = mockReviews.filter((r) => r.projectId === project.id).slice(0, 4);

  return (
    <div>
      <PageHeader
        title={project.name}
        description={project.description}
        actions={<ProjectStatusBadge status={project.status} />}
      />
      <Suspense fallback={null}>
        <WorkspaceTabs projectId={project.id} />
      </Suspense>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Progress</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-baseline justify-between">
              <span className="text-2xl font-semibold">{completedCount}</span>
              <span className="text-xs text-muted-foreground">of {nodes.length} stages complete</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-primary" style={{ width: `${progressPct}%` }} />
            </div>
            <dl className="grid grid-cols-2 gap-2 pt-2 text-xs">
              <div>
                <dt className="text-muted-foreground">Business owner</dt>
                <dd className="font-medium">{project.businessOwner}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Updated</dt>
                <dd className="font-medium">{formatDate(project.updatedAt)}</dd>
              </div>
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">Documents</CardTitle>
            <Link href={`/documents?project=${project.id}`} className="text-xs text-muted-foreground hover:underline">
              View all
            </Link>
          </CardHeader>
          <CardContent className="space-y-2">
            {documents.length === 0 ? (
              <p className="text-xs text-muted-foreground">No documents yet.</p>
            ) : (
              documents.map((doc) => (
                <div key={doc.id} className="flex items-center justify-between gap-2 text-sm">
                  <span className="truncate">{doc.title}</span>
                  <WorkflowStatusBadge status={doc.status} className="shrink-0" />
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">Reviews</CardTitle>
            <Link href={`/reviews?project=${project.id}`} className="text-xs text-muted-foreground hover:underline">
              View all
            </Link>
          </CardHeader>
          <CardContent className="space-y-2">
            {reviews.length === 0 ? (
              <p className="text-xs text-muted-foreground">No reviews yet.</p>
            ) : (
              reviews.map((review) => (
                <div key={review.id} className="flex items-center justify-between gap-2 text-sm">
                  <span className="truncate">{review.artifactTitle}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{review.status}</span>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
