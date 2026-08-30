"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Library } from "lucide-react";

import { KnowledgeSourceStatusBadge } from "@/components/status-badge";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState } from "@/components/ui/empty-state";
import { formatDate } from "@/lib/format";
import type { KnowledgeSourceItem } from "@/lib/types";

const SOURCE_TYPE_LABEL: Record<KnowledgeSourceItem["sourceType"], string> = {
  PROJECT_ARTIFACT: "Project artifact",
  UPLOADED_DOCUMENT: "Uploaded document",
  EXTERNAL_LINK: "External link",
};

export function KnowledgeSourceTable({ sources }: { sources: KnowledgeSourceItem[] }) {
  const categories = useMemo(() => Array.from(new Set(sources.map((s) => s.category))).sort(), [sources]);
  const [category, setCategory] = useState<string | "ALL">("ALL");

  const filtered = useMemo(
    () => (category === "ALL" ? sources : sources.filter((s) => s.category === category)),
    [sources, category]
  );

  return (
    <div>
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <Select value={category} onChange={(e) => setCategory(e.target.value)} className="sm:max-w-[220px]">
          <option value="ALL">All categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
      </div>

      {filtered.length === 0 ? (
        <EmptyState icon={Library} title="No knowledge sources found" description="Try a different category filter." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead>Category</TableHead>
              <TableHead>Source type</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Chunks</TableHead>
              <TableHead>Uploaded by</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((source) => (
              <TableRow key={source.id}>
                <TableCell className="font-medium">
                  <Link href={`/knowledge-base/${source.id}`} className="hover:underline">
                    {source.title}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">{source.category}</TableCell>
                <TableCell className="text-muted-foreground">{SOURCE_TYPE_LABEL[source.sourceType]}</TableCell>
                <TableCell>
                  <KnowledgeSourceStatusBadge status={source.status} />
                </TableCell>
                <TableCell className="text-muted-foreground">{source.chunkCount}</TableCell>
                <TableCell className="text-muted-foreground">{source.uploadedByName}</TableCell>
                <TableCell className="text-muted-foreground">{formatDate(source.createdAt)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
