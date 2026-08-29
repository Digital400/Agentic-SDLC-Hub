"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ClipboardCheck, X } from "lucide-react";

import { ReviewStatusBadge } from "@/components/status-badge";
import { buttonVariants } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate } from "@/lib/format";
import type { ReviewItem, ReviewStatus } from "@/lib/types";

const STATUS_FILTERS: Array<{ label: string; value: ReviewStatus | "ALL" }> = [
  { label: "Pending", value: "PENDING" },
  { label: "All statuses", value: "ALL" },
  { label: "Approved", value: "APPROVED" },
  { label: "Needs changes", value: "NEEDS_CHANGES" },
  { label: "Rejected", value: "REJECTED" },
];

export function ReviewsTable({ reviews }: { reviews: ReviewItem[] }) {
  const searchParams = useSearchParams();
  const projectFilterFromUrl = searchParams.get("project");

  // 1. Pending reviews table — defaults to PENDING, the actionable queue.
  const [status, setStatus] = useState<ReviewStatus | "ALL">("PENDING");
  // 3. Reviewer filter
  const [reviewer, setReviewer] = useState<string | "ALL">("ALL");
  // 4. Project filter — a dropdown, in addition to the ?project= link some
  // pages (e.g. a project workspace) deep-link with.
  const [project, setProject] = useState<string | "ALL">(projectFilterFromUrl ?? "ALL");

  const reviewers = useMemo(() => Array.from(new Set(reviews.map((r) => r.reviewerName))).sort(), [reviews]);
  const projects = useMemo(
    () => Array.from(new Map(reviews.map((r) => [r.projectId, r.projectName])).entries()),
    [reviews]
  );

  const filtered = useMemo(() => {
    return reviews.filter((r) => {
      const matchesStatus = status === "ALL" || r.status === status;
      const matchesReviewer = reviewer === "ALL" || r.reviewerName === reviewer;
      const matchesProject = project === "ALL" || r.projectId === project;
      return matchesStatus && matchesReviewer && matchesProject;
    });
  }, [reviews, status, reviewer, project]);

  const activeProjectName = projects.find(([id]) => id === project)?.[1];

  return (
    <div>
      {/* 2. Review status filter, 3. Reviewer filter, 4. Project filter */}
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <Select value={status} onChange={(e) => setStatus(e.target.value as ReviewStatus | "ALL")} className="sm:max-w-[170px]">
          {STATUS_FILTERS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>

        <Select value={reviewer} onChange={(e) => setReviewer(e.target.value)} className="sm:max-w-[170px]">
          <option value="ALL">All reviewers</option>
          {reviewers.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </Select>

        <Select value={project} onChange={(e) => setProject(e.target.value)} className="sm:max-w-[220px]">
          <option value="ALL">All projects</option>
          {projects.map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </Select>

        {project !== "ALL" ? (
          <button
            onClick={() => setProject("ALL")}
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            {activeProjectName}
            <X className="h-3 w-3" />
          </button>
        ) : null}
      </div>

      {filtered.length === 0 ? (
        <EmptyState icon={ClipboardCheck} title="No reviews found" description="Try a different status, reviewer, or project filter." />
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
                <TableCell className="font-medium">
                  <Link href={`/reviews/${review.id}`} className="hover:underline">
                    {review.artifactTitle}
                  </Link>
                </TableCell>
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
