"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { X } from "lucide-react";

import { WorkflowStatusBadge } from "@/components/status-badge";
import { buttonVariants } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate } from "@/lib/format";
import type { DocumentArtifact } from "@/lib/types";
import { FileText } from "lucide-react";

export function DocumentsTable({ documents }: { documents: DocumentArtifact[] }) {
  const searchParams = useSearchParams();
  const projectFilter = searchParams.get("project");
  const [query, setQuery] = useState("");

  const scoped = useMemo(
    () => (projectFilter ? documents.filter((d) => d.projectId === projectFilter) : documents),
    [documents, projectFilter]
  );
  const projectName = scoped[0]?.projectName;

  const filtered = useMemo(
    () => scoped.filter((d) => query.trim() === "" || d.title.toLowerCase().includes(query.toLowerCase())),
    [scoped, query]
  );

  return (
    <div>
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <Input
          placeholder="Search documents…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="sm:max-w-xs"
        />
        {projectFilter ? (
          <Link href="/documents" className={buttonVariants({ variant: "outline", size: "sm" })}>
            {projectName ?? "Filtered project"}
            <X className="h-3 w-3" />
          </Link>
        ) : null}
      </div>

      {filtered.length === 0 ? (
        <EmptyState icon={FileText} title="No documents found" description="Try a different search or clear the project filter." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead>Project</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Version</TableHead>
              <TableHead>Updated</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((doc) => (
              <TableRow key={doc.id}>
                <TableCell className="font-medium">{doc.title}</TableCell>
                <TableCell className="text-muted-foreground">
                  <Link href={`/projects/${doc.projectId}`} className="hover:underline">
                    {doc.projectName}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">{doc.artifactType.replaceAll("_", " ")}</TableCell>
                <TableCell>
                  <WorkflowStatusBadge status={doc.status} />
                </TableCell>
                <TableCell className="text-muted-foreground">v{doc.versionNumber}</TableCell>
                <TableCell className="text-muted-foreground">{formatDate(doc.updatedAt)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
