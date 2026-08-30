import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, FileText } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { KnowledgeSourceStatusBadge } from "@/components/status-badge";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { formatDate } from "@/lib/format";
import { api, ApiError } from "@/lib/api";
import { toKnowledgeChunkItem, toKnowledgeSourceItem } from "@/lib/mappers";

const SOURCE_TYPE_LABEL: Record<string, string> = {
  PROJECT_ARTIFACT: "Project artifact",
  UPLOADED_DOCUMENT: "Uploaded document",
  EXTERNAL_LINK: "External link",
};

export default async function KnowledgeSourceChunksPage({ params }: { params: { sourceId: string } }) {
  let apiSource;
  try {
    apiSource = await api.knowledgeSources.get(params.sourceId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  const source = toKnowledgeSourceItem(apiSource);
  const apiChunks = await api.knowledgeSources.chunks(source.id);
  const chunks = apiChunks.map(toKnowledgeChunkItem);

  return (
    <div>
      <Link href="/knowledge-base" className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" />
        Back to Knowledge Base
      </Link>

      <PageHeader
        title={source.title}
        description={`${source.category} · ${SOURCE_TYPE_LABEL[source.sourceType]} · uploaded by ${source.uploadedByName} on ${formatDate(source.createdAt)}`}
        actions={<KnowledgeSourceStatusBadge status={source.status} />}
      />

      {source.fileUrl ? (
        <p className="mb-4 text-sm text-muted-foreground">
          Source URL:{" "}
          <a href={source.fileUrl} target="_blank" rel="noreferrer" className="text-foreground hover:underline">
            {source.fileUrl}
          </a>
        </p>
      ) : null}

      <h2 className="mb-3 text-sm font-semibold">Chunks ({chunks.length})</h2>

      {chunks.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No chunks yet"
          description="This source hasn't been chunked — no ingestion pipeline is wired up yet."
        />
      ) : (
        <div className="flex flex-col gap-3">
          {chunks.map((chunk) => (
            <Card key={chunk.id}>
              <CardContent className="p-4">
                <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">Chunk {chunk.chunkIndex}</span>
                  <span>{formatDate(chunk.createdAt)}</span>
                </div>
                <p className="whitespace-pre-wrap text-sm">{chunk.content}</p>
                {chunk.metadataJson ? (
                  <pre className="mt-3 overflow-x-auto rounded-md bg-muted/50 p-2 text-xs text-muted-foreground">
                    {JSON.stringify(chunk.metadataJson, null, 2)}
                  </pre>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
