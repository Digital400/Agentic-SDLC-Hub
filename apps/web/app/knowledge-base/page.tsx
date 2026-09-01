import { Info } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { KnowledgeSourceTable } from "@/components/knowledge/knowledge-source-table";
import { UploadDocumentButton } from "@/components/knowledge/upload-document-button";
import { api } from "@/lib/api";
import { toKnowledgeSourceItem } from "@/lib/mappers";

export default async function KnowledgeBasePage() {
  const [apiSources, users] = await Promise.all([api.knowledgeSources.list(), api.users.list()]);
  const sources = apiSources.map(toKnowledgeSourceItem);
  const defaultUserId = users[0]?.id ?? null;

  return (
    <div>
      <PageHeader
        title="Knowledge Base"
        description="Sources agents retrieve from during a run — company standards, past artifacts, UI guidelines, architecture rules, and testing standards."
        actions={<UploadDocumentButton uploadedById={defaultUserId} />}
      />

      <div className="mb-4 flex items-start gap-2 rounded-lg border border-border bg-muted/50 p-3 text-xs text-muted-foreground">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p>
          Uploaded documents are extracted, chunked, and embedded immediately, so they&apos;re retrievable right away —
          see the Ops dashboard&apos;s &quot;Most used RAG sources&quot; for which ones agent runs actually cite.
        </p>
      </div>

      <KnowledgeSourceTable sources={sources} />
    </div>
  );
}
