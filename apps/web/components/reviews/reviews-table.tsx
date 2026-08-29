"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { X } from "lucide-react";

import { ReviewStatusBadge } from "@/components/status-badge";
import { buttonVariants } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ClipboardCheck } from "lucide-react";
import { formatDate } from "@/lib/format";
import type { ReviewItem, ReviewStatus } from "@/lib/types";

const STATUS_FILTERS: Array<{ label: string; value: ReviewStatus | "ALL" }> = [
  { label: "All statuses", value: "ALL" },
  { label: "Pending", value: "PENDING" },
  { label: "Approved", value: "APPROVED" },
  { label: "Needs changes", value: "NEEDS_CHANGES" },
  { label: "Rejected", value: "REJECTED" },
];

export function ReviewsTable({ reviews }: { reviews: ReviewItem[] }) {
  const searchParams = useSearchParams();
  const projectFilter = searchParams.get("project");
  const [status, setStatus] = useState<ReviewStatus | "ALL">("ALL");

  const scoped = useMemo(
    () => (projectFilter ? reviews.filter((r) => r.projectId === projectFilter) : reviews),
    [reviews, projectFilter]
  );
  const projectName = scoped[0]?.projectName;

  const filtered = useMemo(
    () => scoped.filter((r) => status === "ALL" || r.status === status),
    [scoped, status]
  );

  return (
    <div>
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <Select value={status} onChange={(e) => setStatus(e.target.value as ReviewStatus | "ALL")} className="sm:max-w-[180px]">
          {STATUS_FILTERS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        {projectFilter ? (
          <Link href="/reviews" className={buttonVariants({ variant: "outline", size: "sm" })}>
            {projectName ?? "Filtered project"}
            <X className="h-3 w-3" />
          </Link>
        ) : null}
      </div>

      {filtered.length === 0 ? (
        <EmptyState icon={ClipboardCheck} title="No reviews found" description="Try a different status or clear the project filter." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Artifact</TableHead>
              <TableHead>Project</TableHead>
              <TableHead>Stage</TableHead>
              <TableHead>Reviewer</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Submitted</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((review) => (
              <TableRow key={review.id}>
                <TableCell className="font-medium">{review.artifactTitle}</TableCell>
                <TableCell className="text-muted-foreground">
                  <Link href={`/projects/${review.projectId}`} className="hover:underline">
                    {review.projectName}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">{review.workflowStageName}</TableCell>
                <TableCell className="text-muted-foreground">{review.reviewerName}</TableCell>
                <TableCell>
                  <ReviewStatusBadge status={review.status} />
                </TableCell>
                <TableCell className="text-muted-foreground">{formatDate(review.submittedAt)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
