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

  const [versions, apiReviews, users] = await Promise.all([
    api.artifacts.versions(artifact.id),
    api.reviews.listAll(),
    api.users.list(),
  ]);

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

  const document = toArtifactDocument(artifact, versions, currentVersion?.content_markdown ?? "", comments);
  const defaultUserId = users[0]?.id ?? null;

  return <ArtifactEditor document={document} createdById={defaultUserId} />;
}
