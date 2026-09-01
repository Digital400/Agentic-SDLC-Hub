"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Loader2, ShieldCheck, Sparkles } from "lucide-react";

import { useRouter } from "next/navigation";

import { ArtifactPreview } from "@/components/reviews/artifact-preview";
import { DecisionHistory } from "@/components/reviews/decision-history";
import { ReviewComments } from "@/components/reviews/review-comments";
import { ReviewDecisionPanel, type ReviewDecision, type StructuredComment } from "@/components/reviews/review-decision-panel";
import { ReviewerChecklist } from "@/components/reviews/reviewer-checklist";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ReviewStatusBadge } from "@/components/status-badge";
import { REVIEW_CHECKLIST_ITEMS } from "@/lib/mock-data";
import { formatDate } from "@/lib/format";
import { api, ApiError } from "@/lib/api";
import { toRevisionAgentRunResult } from "@/lib/mappers";
import type {
  ArtifactCommentItem,
  ReviewDecisionHistoryEntry,
  ReviewDetail as ReviewDetailData,
  ReviewStatus,
  RevisionAgentRunResult,
} from "@/lib/types";

export function ReviewDetail({
  review: initial,
  currentUserId,
}: {
  review: ReviewDetailData;
  /** Attributes the revision agent run and its new artifact version — see
   * app/layout.tsx's "no login yet" convention, reused here as elsewhere. */
  currentUserId: string | null;
}) {
  const router = useRouter();
  const [status, setStatus] = useState<ReviewStatus>(initial.status);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [comments, setComments] = useState<ArtifactCommentItem[]>(initial.artifact.comments);
  const [history, setHistory] = useState<ReviewDecisionHistoryEntry[]>(initial.history);
  const [decidedAt, setDecidedAt] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [revisionRunning, setRevisionRunning] = useState(false);
  const [revisionResult, setRevisionResult] = useState<RevisionAgentRunResult | null>(null);
  const [revisionError, setRevisionError] = useState<string | null>(null);

  const allChecked = useMemo(() => REVIEW_CHECKLIST_ITEMS.every((item) => checked[item.id]), [checked]);
  const sectionTitles = useMemo(() => initial.artifact.sections.map((s) => s.title), [initial.artifact.sections]);

  function toggleChecklistItem(id: string) {
    setChecked((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  async function handleDecide(decision: Exclude<ReviewDecision, "NEEDS_CHANGES">, comment: string | null) {
    try {
      const decided = decision === "APPROVED"
        ? await api.reviews.approve(initial.id, comment ?? undefined)
        : await api.reviews.reject(initial.id, comment ?? "");

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

      setBanner(decision === "APPROVED" ? "Approved." : "Rejected.");
      router.refresh();
    } catch (err) {
      setBanner(err instanceof ApiError ? `Failed to record decision: ${err.message}` : "Failed to record decision.");
    } finally {
      window.setTimeout(() => setBanner(null), 4000);
    }
  }

  async function handleRequestChanges(structuredComments: StructuredComment[]) {
    try {
      const decided = await api.reviews.requestChanges(
        initial.id,
        structuredComments.map((c) => ({ body: c.body, section_title: c.sectionTitle }))
      );

      const now = decided.decided_at ?? new Date().toISOString();
      setStatus(decided.status);
      setDecidedAt(now);
      setComments(
        decided.comments.map((c) => ({
          id: c.id,
          authorName: initial.reviewerName,
          body: c.body,
          createdAt: c.created_at,
          sectionTitle: c.section_title,
        }))
      );
      setHistory((prev) => [
        ...prev,
        {
          id: `${initial.id}-decision`,
          versionNumber: initial.artifact.currentVersionNumber,
          status: "NEEDS_CHANGES",
          reviewerName: initial.reviewerName,
          comment: structuredComments[0]?.body ?? null,
          decidedAt: now,
        },
      ]);
      setRevisionResult(null);
      setBanner("Changes requested.");
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

  async function handleRunRevisionAgent() {
    if (currentUserId === null) {
      setRevisionError("No users exist yet to attribute this run to.");
      return;
    }
    setRevisionRunning(true);
    setRevisionError(null);
    try {
      const response = await api.reviews.runRevisionAgent(initial.id, currentUserId);
      setRevisionResult(toRevisionAgentRunResult(response));
    } catch (err) {
      setRevisionError(err instanceof ApiError ? err.message : "Failed to run the revision agent.");
    } finally {
      setRevisionRunning(false);
    }
  }

  const isPending = status === "PENDING";
  // The revision agent only applies to this exact round while it's still
  // the one a human sent back — once run, a new PENDING review exists and
  // this one stays NEEDS_CHANGES permanently as history (see
  // app/services/revision_agent.py).
  const canRunRevisionAgent = status === "NEEDS_CHANGES" && revisionResult === null;

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
            sectionTitles={sectionTitles}
            onDecide={handleDecide}
            onRequestChanges={handleRequestChanges}
          />

          {status === "NEEDS_CHANGES" ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">Revision agent</CardTitle>
                <CardDescription>
                  Revises only the section(s) the comments above named, using this stage&rsquo;s current content, the
                  reviewer feedback, and its last validation result — then resubmits for review.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-2">
                {revisionResult === null ? (
                  <Button size="sm" onClick={handleRunRevisionAgent} disabled={!canRunRevisionAgent || revisionRunning}>
                    {revisionRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                    {revisionRunning ? "Running…" : "Run revision agent"}
                  </Button>
                ) : revisionResult.needsClarification ? (
                  <p className="text-xs text-muted-foreground">
                    The agent needs more information before it can revise this artifact —{" "}
                    <Link href={`/agent-runs/${revisionResult.agentRunId}`} className="underline">
                      view the run
                    </Link>{" "}
                    to see its questions.
                  </p>
                ) : (
                  <div className="text-xs text-muted-foreground">
                    <p>
                      Updated section{revisionResult.sectionsUpdated.length === 1 ? "" : "s"}:{" "}
                      {revisionResult.sectionsUpdated.join(", ") || "(none)"}.
                    </p>
                    {revisionResult.newReviewId ? (
                      <Link href={`/reviews/${revisionResult.newReviewId}`} className="mt-1 inline-block underline">
                        View the resubmitted review
                      </Link>
                    ) : null}
                  </div>
                )}
                {revisionError ? <p className="text-xs text-destructive">{revisionError}</p> : null}
              </CardContent>
            </Card>
          ) : null}

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
