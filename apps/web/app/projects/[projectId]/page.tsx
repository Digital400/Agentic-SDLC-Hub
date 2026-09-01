import Link from "next/link";
import { Suspense } from "react";
import { notFound } from "next/navigation";

import { PageHeader } from "@/components/layout/page-header";
import { WorkspaceTabs } from "@/components/projects/workspace-tabs";
import { ArtifactStatusBadge, ProjectStatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDate } from "@/lib/format";
import { api, ApiError } from "@/lib/api";
import { toDocumentArtifact, toProject, toReviewItem, toWorkflowNode } from "@/lib/mappers";

export default async function ProjectWorkspacePage({ params }: { params: { projectId: string } }) {
  let project;
  try {
    project = toProject(await api.projects.get(params.projectId));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [apiNodes, apiArtifacts, apiReviews] = await Promise.all([
    api.projects.workflowNodes(project.id),
    api.projects.artifacts(project.id),
    api.reviews.listAll(),
  ]);

  const nodes = apiNodes.map(toWorkflowNode);
  // A stage counts as done once it reaches any of the graph engine's own
  // "satisfied predecessor" statuses (see
  // apps/api/app/services/graph_engine.py's _SATISFIED_PREDECESSOR_STATUSES):
  // APPROVED for a stage that went through human review (most stages),
  // COMPLETED for one that doesn't require approval (e.g. Implementation),
  // or SKIPPED via a manual override. Counting only COMPLETED here would
  // permanently show 0/11 for a normal project, since most stages finish
  // as APPROVED, not COMPLETED.
  const DONE_STATUSES = new Set(["APPROVED", "COMPLETED", "SKIPPED"]);
  const completedCount = nodes.filter((n) => DONE_STATUSES.has(n.status)).length;
  const progressPct = nodes.length > 0 ? Math.round((completedCount / nodes.length) * 100) : 0;

  const documents = apiArtifacts.map(toDocumentArtifact).slice(0, 4);
  const reviews = apiReviews
    .filter((r) => r.project_id === project.id)
    .map(toReviewItem)
    .slice(0, 4);

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
                <Link
                  key={doc.id}
                  href={`/documents/${doc.id}`}
                  className="flex items-center justify-between gap-2 text-sm hover:underline"
                >
                  <span className="truncate">{doc.title}</span>
                  <ArtifactStatusBadge status={doc.status} className="shrink-0" />
                </Link>
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
