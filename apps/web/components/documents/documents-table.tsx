"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { ArtifactStatusBadge } from "@/components/status-badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate } from "@/lib/format";
import type { DocumentArtifact } from "@/lib/types";
import { FileText } from "lucide-react";

export function DocumentsTable({ documents }: { documents: DocumentArtifact[] }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const projectFilter = searchParams.get("project");
  const [query, setQuery] = useState("");

  // One entry per project actually represented in `documents`, sorted by
  // name — derived here rather than passed in separately, since a
  // project with zero documents has nothing to filter down to anyway.
  const projectOptions = useMemo(() => {
    const byId = new Map<string, string>();
    for (const d of documents) byId.set(d.projectId, d.projectName);
    return Array.from(byId, ([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [documents]);

  const scoped = useMemo(
    () => (projectFilter ? documents.filter((d) => d.projectId === projectFilter) : documents),
    [documents, projectFilter]
  );

  const filtered = useMemo(
    () => scoped.filter((d) => query.trim() === "" || d.title.toLowerCase().includes(query.toLowerCase())),
    [scoped, query]
  );

  function handleProjectChange(value: string) {
    router.push(value ? `/documents?project=${value}` : "/documents");
  }

  return (
    <div>
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <Input
          placeholder="Search documents…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="sm:max-w-xs"
        />
        <Select value={projectFilter ?? ""} onChange={(e) => handleProjectChange(e.target.value)} className="sm:max-w-xs">
          <option value="">All projects</option>
          {projectOptions.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </Select>
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
                <TableCell className="font-medium">
                  <Link href={`/documents/${doc.id}`} className="hover:underline">
                    {doc.title}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  <Link href={`/projects/${doc.projectId}`} className="hover:underline">
                    {doc.projectName}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">{doc.artifactType.replaceAll("_", " ")}</TableCell>
                <TableCell>
                  <ArtifactStatusBadge status={doc.status} />
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
