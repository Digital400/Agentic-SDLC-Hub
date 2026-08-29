import { Info } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate } from "@/lib/format";
import { mockKnowledgeBase } from "@/lib/mock-data";

export default function KnowledgeBasePage() {
  return (
    <div>
      <PageHeader
        title="Knowledge Base"
        description="Sources available for agents to ground their drafts in — retrieval isn't wired up yet."
      />

      <div className="mb-4 flex items-start gap-2 rounded-lg border border-border bg-muted/50 p-3 text-xs text-muted-foreground">
        <Info className="mt-0.5 h-4 w-4 shrink-0" />
        <p>
          Retrieval-augmented generation (RAG) is on the roadmap, not built yet — see docs/mvp-plan.md. This list
          shows what would be indexed once pgvector is introduced.
        </p>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Title</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Project</TableHead>
            <TableHead>Indexed</TableHead>
            <TableHead>Updated</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {mockKnowledgeBase.map((source) => (
            <TableRow key={source.id}>
              <TableCell className="font-medium">{source.title}</TableCell>
              <TableCell className="text-muted-foreground">{source.sourceType}</TableCell>
              <TableCell className="text-muted-foreground">{source.projectName ?? "—"}</TableCell>
              <TableCell>
                <Badge variant={source.indexed ? "success" : "outline"}>
                  {source.indexed ? "Indexed" : "Not indexed"}
                </Badge>
              </TableCell>
              <TableCell className="text-muted-foreground">{formatDate(source.updatedAt)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
