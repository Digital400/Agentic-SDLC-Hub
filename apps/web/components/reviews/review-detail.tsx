"use client";

import { useMemo, useState } from "react";
import { ShieldCheck } from "lucide-react";

import { useRouter } from "next/navigation";

import { ArtifactPreview } from "@/components/reviews/artifact-preview";
import { DecisionHistory } from "@/components/reviews/decision-history";
import { ReviewComments } from "@/components/reviews/review-comments";
import { ReviewDecisionPanel, type ReviewDecision } from "@/components/reviews/review-decision-panel";
import { ReviewerChecklist } from "@/components/reviews/reviewer-checklist";
import { ReviewStatusBadge } from "@/components/status-badge";
import { REVIEW_CHECKLIST_ITEMS } from "@/lib/mock-data";
import { formatDate } from "@/lib/format";
import { api, ApiError } from "@/lib/api";
import type { ArtifactCommentItem, ReviewDecisionHistoryEntry, ReviewDetail as ReviewDetailData, ReviewStatus } from "@/lib/types";

export function ReviewDetail({ review: initial }: { review: ReviewDetailData }) {
  const router = useRouter();
  const [status, setStatus] = useState<ReviewStatus>(initial.status);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [comments, setComments] = useState<ArtifactCommentItem[]>(initial.artifact.comments);
  const [history, setHistory] = useState<ReviewDecisionHistoryEntry[]>(initial.history);
  const [decidedAt, setDecidedAt] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const allChecked = useMemo(() => REVIEW_CHECKLIST_ITEMS.every((item) => checked[item.id]), [checked]);

  function toggleChecklistItem(id: string) {
    setChecked((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  async function handleDecide(decision: ReviewDecision, comment: string | null) {
    try {
      let decided;
      if (decision === "APPROVED") {
        decided = await api.reviews.approve(initial.id, comment ?? undefined);
      } else if (decision === "NEEDS_CHANGES") {
        decided = await api.reviews.requestChanges(initial.id, comment ?? "");
      } else {
        decided = await api.reviews.reject(initial.id, comment ?? "");
      }

      const now = decided.decided_at ?? new Date().toISOString();
      setStatus(decided.status);
      setDecidedAt(now);
      setHistory((prev) => [
        ...prev,
        {
          id: `${initial.id}-decision`,
          versionNumber: initial.artifact.currentVersionNumber,
          status: decision,
          reviewerName: initial.reviewerName,
          comment,
          decidedAt: now,
        },
      ]);

      const labels: Record<ReviewDecision, string> = {
        APPROVED: "Approved.",
        NEEDS_CHANGES: "Changes requested.",
        REJECTED: "Rejected.",
      };
      setBanner(labels[decision]);
      router.refresh();
    } catch (err) {
      setBanner(err instanceof ApiError ? `Failed to record decision: ${err.message}` : "Failed to record decision.");
    } finally {
      window.setTimeout(() => setBanner(null), 4000);
    }
  }

  async function handleAddComment(body: string) {
    try {
      const comment = await api.reviews.addComment(initial.id, { author_id: initial.reviewerId, body });
      setComments((prev) => [
        ...prev,
        { id: comment.id, authorName: initial.reviewerName, body: comment.body, createdAt: comment.created_at },
      ]);
    } catch (err) {
      setBanner(err instanceof ApiError ? `Failed to post comment: ${err.message}` : "Failed to post comment.");
      window.setTimeout(() => setBanner(null), 4000);
    }
  }

  const isPending = status === "PENDING";

  return (
    <div>
      {/* Header — makes the human-approval context unambiguous: who is
          reviewing, what, and its current state. */}
      <div className="mb-6 flex flex-col gap-2 border-b border-border pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-primary" />
            <h1 className="text-lg font-semibold">Review: {initial.artifactTitle}</h1>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {initial.projectName} · {initial.workflowStageName} · Submitted {formatDate(initial.submittedAt)}
          </p>
        </div>
        <div className="flex flex-col items-start gap-1 sm:items-end">
          <ReviewStatusBadge status={status} />
          <p className="text-xs text-muted-foreground">
            Reviewer: <span className="font-medium text-foreground">{initial.reviewerName}</span>
          </p>
          {decidedAt ? <p className="text-xs text-muted-foreground">Decided {formatDate(decidedAt)}</p> : null}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="flex flex-col gap-4 xl:col-span-2">
          <ArtifactPreview artifact={initial.artifact} />
        </div>

        <div className="flex flex-col gap-4">
          <ReviewerChecklist checked={checked} onToggle={toggleChecklistItem} />
          <ReviewDecisionPanel
            disabled={!isPending}
            disabledReason={!isPending ? `This review was already decided (${status.replace(/_/g, " ").toLowerCase()}).` : undefined}
            approveDisabled={!allChecked}
            approveDisabledReason="Check every item in the reviewer checklist before approving."
            onDecide={handleDecide}
          />
          <ReviewComments comments={comments} onAddComment={handleAddComment} />
          <DecisionHistory history={history} />
        </div>
      </div>

      {banner ? (
        <div className="fixed bottom-6 right-6 z-50 rounded-md bg-foreground px-4 py-2 text-sm text-background shadow-lg">
          {banner}
        </div>
      ) : null}
    </div>
  );
}
