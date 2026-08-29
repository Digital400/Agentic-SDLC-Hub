import Link from "next/link";
import { X } from "lucide-react";

import { ReviewStatusBadge, WorkflowStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { formatRelativeTime, formatSnakeCase } from "@/lib/format";
import type { DocumentArtifact, ProjectWorkflowNode, ReviewItem } from "@/lib/types";

export function NodeDetailsPanel({
  projectId,
  node,
  documents,
  reviews,
  onClose,
}: {
  projectId: string;
  node: ProjectWorkflowNode;
  documents: DocumentArtifact[];
  reviews: ReviewItem[];
  onClose: () => void;
}) {
  const artifact = documents.find((d) => d.projectId === projectId && d.artifactType === node.outputArtifactType);

  const review = reviews
    .filter((r) => r.projectId === projectId && r.workflowStageName === node.name)
    .sort((a, b) => (a.submittedAt < b.submittedAt ? 1 : -1))[0];

  return (
    <aside className="flex w-80 shrink-0 flex-col overflow-y-auto rounded-lg border border-border bg-card">
      <div className="flex items-start justify-between gap-2 border-b border-border p-4">
        <div>
          <h2 className="text-sm font-semibold">{node.name}</h2>
          <div className="mt-1.5">
            <WorkflowStatusBadge status={node.status} />
          </div>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close panel" className="h-7 w-7 shrink-0">
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex flex-col gap-4 p-4 text-sm">
        <section>
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Description</h3>
          <p className="text-sm">{node.description}</p>
        </section>

        <Separator />

        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Required inputs</h3>
          {node.requiredInputs.length === 0 ? (
            <p className="text-xs text-muted-foreground">None — this is the first stage.</p>
          ) : (
            <div className="flex flex-wrap gap-1">
              {node.requiredInputs.map((input) => (
                <Badge key={input} variant="outline">
                  {formatSnakeCase(input)}
                </Badge>
              ))}
            </div>
          )}
        </section>

        <Separator />

        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Output artifact</h3>
          <p className="text-sm">{formatSnakeCase(node.outputArtifactType)}</p>
          {artifact ? (
            <div className="mt-2 rounded-md border border-border p-2">
              <p className="truncate text-xs font-medium">{artifact.title}</p>
              <p className="text-xs text-muted-foreground">
                v{artifact.versionNumber} · updated {formatRelativeTime(artifact.updatedAt)}
              </p>
              <Link
                href={`/documents?project=${projectId}`}
                className={buttonVariants({ variant: "outline", size: "sm", className: "mt-2 w-full" })}
              >
                Open artifact
              </Link>
            </div>
          ) : (
            <Button variant="outline" size="sm" className="mt-2 w-full" disabled>
              No artifact produced yet
            </Button>
          )}
        </section>

        <Separator />

        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Allowed actions</h3>
          <div className="flex flex-wrap gap-1">
            {node.allowedActions.map((action) => (
              <Badge key={action} variant="secondary">
                {formatSnakeCase(action)}
              </Badge>
            ))}
          </div>
        </section>

        <Separator />

        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Review status</h3>
          {review ? (
            <div className="flex flex-col gap-1">
              <ReviewStatusBadge status={review.status} className="w-fit" />
              <p className="text-xs text-muted-foreground">
                {review.reviewerName} · submitted {formatRelativeTime(review.submittedAt)}
              </p>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              {node.requiresHumanApproval ? "No review submitted yet." : "This stage doesn't require human approval."}
            </p>
          )}
        </section>
      </div>
    </aside>
  );
}
