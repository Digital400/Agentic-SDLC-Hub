import Link from "next/link";
import { ClipboardCheck } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { buttonVariants } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatRelativeTime } from "@/lib/format";
import type { ReviewItem } from "@/lib/types";

// A compact, action-oriented view of only the reviews waiting on someone
// right now — the full filterable table lives on /reviews.
export function PendingReviewsTable({ reviews }: { reviews: ReviewItem[] }) {
  const pending = reviews.filter((r) => r.status === "PENDING");

  if (pending.length === 0) {
    return (
      <EmptyState
        icon={ClipboardCheck}
        title="Nothing waiting on review"
        description="Every submitted artifact has been reviewed."
        className="border-none py-8"
      />
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Artifact</TableHead>
          <TableHead>Project</TableHead>
          <TableHead>Stage</TableHead>
          <TableHead>Reviewer</TableHead>
          <TableHead>Submitted</TableHead>
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>
        {pending.map((review) => (
          <TableRow key={review.id}>
            <TableCell className="font-medium">{review.artifactTitle}</TableCell>
            <TableCell className="text-muted-foreground">{review.projectName}</TableCell>
            <TableCell className="text-muted-foreground">{review.workflowStageName}</TableCell>
            <TableCell className="text-muted-foreground">{review.reviewerName}</TableCell>
            <TableCell className="text-muted-foreground">{formatRelativeTime(review.submittedAt)}</TableCell>
            <TableCell className="text-right">
              <Link href={`/projects/${review.projectId}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
                Open
              </Link>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
