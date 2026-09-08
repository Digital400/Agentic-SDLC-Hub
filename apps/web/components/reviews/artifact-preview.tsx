import Link from "next/link";
import { ExternalLink } from "lucide-react";

import { MarkdownPreview } from "@/components/documents/markdown-preview";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatRelativeTime, formatSnakeCase } from "@/lib/format";
import type { ArtifactDocument } from "@/lib/types";

// 1. Artifact preview, 2. Version information. Read-only by design — this
// screen is for deciding on the artifact, not editing it (see the full
// editor at /documents/[artifactId] for that).
export function ArtifactPreview({ artifact }: { artifact: ArtifactDocument }) {
  const version = artifact.versions.find((v) => v.versionNumber === artifact.currentVersionNumber);

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle className="text-sm">Version information</CardTitle>
          <Link href={`/documents/${artifact.id}`} className="text-xs text-muted-foreground hover:underline">
            Open in editor
            <ExternalLink className="ml-1 inline h-3 w-3" />
          </Link>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-xs text-muted-foreground">Version</dt>
              <dd className="font-medium">v{artifact.currentVersionNumber}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Author</dt>
              <dd className="font-medium">{version?.createdByName ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Updated</dt>
              <dd className="font-medium">{version ? formatRelativeTime(version.createdAt) : "—"}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Artifact type</dt>
              <dd className="font-medium">{formatSnakeCase(artifact.artifactType)}</dd>
            </div>
          </dl>
          {version?.changeSummary ? (
            <p className="mt-3 border-t border-border pt-3 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">What changed: </span>
              {version.changeSummary}
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">{artifact.title}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          {artifact.sections.map((section) => (
            <div key={section.id}>
              <h3 className="mb-1.5 text-sm font-semibold">{section.title}</h3>
              <MarkdownPreview markdown={section.contentMarkdown} />
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
