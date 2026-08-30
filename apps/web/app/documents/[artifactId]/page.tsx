import { notFound } from "next/navigation";

import { ArtifactEditor } from "@/components/documents/artifact-editor";
import { api, ApiError } from "@/lib/api";
import { toArtifactDocument } from "@/lib/mappers";
import type { ArtifactCommentItem } from "@/lib/types";

export default async function ArtifactEditorPage({ params }: { params: { artifactId: string } }) {
  let artifact;
  try {
    artifact = await api.artifacts.get(params.artifactId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const [versions, apiReviews, users, workflowNodes] = await Promise.all([
    api.artifacts.versions(artifact.id),
    api.reviews.listAll(),
    api.users.list(),
    api.projects.workflowNodes(artifact.project_id),
  ]);

  const node = workflowNodes.find((n) => n.id === artifact.workflow_node_id);
  // Mirrors the backend's own required-input classification (see
  // apps/api/app/services/workflow_progress.py's resolve_required_inputs):
  // a required input that matches some node's output_artifact_type is an
  // upstream-artifact dependency (already satisfied via approval, no user
  // input needed); anything else is freeform and needs a text box.
  const knownArtifactTypes = new Set(workflowNodes.map((n) => n.output_artifact_type));
  const freeformInputKeys = (node?.required_inputs ?? []).filter((input) => !knownArtifactTypes.has(input));

  const currentVersion = versions.find((v) => v.id === artifact.current_version_id);
  const userNameById = new Map(users.map((u) => [u.id, u.full_name]));

  // The document editor shows comments made across every review round this
  // artifact's versions have gone through — a review's comments live under
  // its own id, so gather them from every review whose artifact matches.
  const comments: ArtifactCommentItem[] = apiReviews
    .filter((r) => r.artifact_id === artifact.id)
    .flatMap((r) => r.comments)
    .sort((a, b) => (a.created_at < b.created_at ? -1 : 1))
    .map((c) => ({
      id: c.id,
      authorName: userNameById.get(c.author_id) ?? "Unknown",
      body: c.body,
      createdAt: c.created_at,
    }));

  const document = toArtifactDocument(
    artifact,
    versions,
    currentVersion?.content_markdown ?? "",
    comments,
    node?.agent_key ?? "",
    freeformInputKeys
  );
  const defaultUserId = users[0]?.id ?? null;
  const reviewers = users.map((u) => ({ id: u.id, name: u.full_name }));

  // "Send for review" submits the artifact AND opens a review round in one
  // action (see ArtifactEditor's handleSendForReview) — but an artifact
  // can be READY_FOR_REVIEW with no open review round behind it (e.g. one
  // sent for review before that pairing existed). Detect that case so the
  // editor can offer to just open the missing review instead of trying to
  // re-submit (which the backend correctly rejects from this status).
  const hasOpenReview = apiReviews.some((r) => r.artifact_id === artifact.id && r.status === "PENDING");

  return (
    <ArtifactEditor
      document={document}
      createdById={defaultUserId}
      reviewers={reviewers}
      hasOpenReview={hasOpenReview}
    />
  );
}
