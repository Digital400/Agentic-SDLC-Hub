import { Info, Upload } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { KnowledgeSourceTable } from "@/components/knowledge/knowledge-source-table";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { toKnowledgeSourceItem } from "@/lib/mappers";

export default async function KnowledgeBasePage() {
  const apiSources = await api.knowledgeSources.list();
  const sources = apiSources.map(toKnowledgeSourceItem);

  return (
    <div>
      <PageHeader
        title="Knowledge Base"
        description="Sources available for agents to ground their drafts in — retrieval isn't wired up yet."
        actions={
          // No upload pipeline exists yet (parsing, chunking, embedding —
          // see apps/api/app/models/knowledge.py). Disabled rather than
          // faking an upload, consistent with the other not-yet-built
          // actions elsewhere in the app (Agent Actions panel, Test Prompt).
          <Button disabled title="File upload isn't available yet.">
            <Upload className="h-4 w-4" />
            Upload document
          </Button>
        }
      />

      <div className="mb-4 flex items-start gap-2 rounded-lg border border-border bg-muted/50 p-3 text-xs text-muted-foreground">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p>
          Retrieval-augmented generation (RAG) is on the roadmap, not built yet — see docs/mvp-plan.md. Sources and
          their chunks are real records now (foundation only); nothing is embedded or retrieved yet.
        </p>
      </div>

      <KnowledgeSourceTable sources={sources} />
    </div>
  );
}
