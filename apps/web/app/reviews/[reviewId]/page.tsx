import { notFound } from "next/navigation";

import { ReviewDetail } from "@/components/reviews/review-detail";
import { api, ApiError } from "@/lib/api";
import { toArtifactDocument, toReviewDetail } from "@/lib/mappers";
import type { ArtifactCommentItem, ReviewDecisionHistoryEntry } from "@/lib/types";

export default async function ReviewDetailPage({ params }: { params: { reviewId: string } }) {
  let review;
  try {
    review = await api.reviews.get(params.reviewId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [artifact, versions, users, allReviews] = await Promise.all([
    api.artifacts.get(review.artifact_id),
    api.artifacts.versions(review.artifact_id),
    api.users.list(),
    api.reviews.listAll(),
  ]);

  const userNameById = new Map(users.map((u) => [u.id, u.full_name]));
  const versionNumberByVersionId = new Map(versions.map((v) => [v.id, v.version_number]));
  const currentVersion = versions.find((v) => v.id === artifact.current_version_id);

  const reviewComments: ArtifactCommentItem[] = review.comments.map((c) => ({
    id: c.id,
    authorName: userNameById.get(c.author_id) ?? "Unknown",
    body: c.body,
    createdAt: c.created_at,
    sectionTitle: c.section_title,
  }));

  // Every other, already-decided round against this same artifact — the
  // approval trail shown below the current round.
  const history: ReviewDecisionHistoryEntry[] = allReviews
    .filter((r) => r.artifact_id === artifact.id && r.id !== review.id && r.status !== "PENDING")
    .map((r) => ({
      id: r.id,
      versionNumber: versionNumberByVersionId.get(r.artifact_version_id) ?? 0,
      status: r.status as Exclude<typeof r.status, "PENDING">,
      reviewerName: r.reviewer_name,
      comment: r.comments.at(-1)?.body ?? null,
      decidedAt: r.decided_at ?? r.updated_at,
    }))
    .sort((a, b) => (a.decidedAt < b.decidedAt ? -1 : 1));

  const artifactDocument = toArtifactDocument(artifact, versions, currentVersion?.content_markdown ?? "", reviewComments);
  const reviewDetail = toReviewDetail(review, artifactDocument, history);
  const currentUserId = users[0]?.id ?? null;

  return <ReviewDetail review={reviewDetail} currentUserId={currentUserId} />;
}
