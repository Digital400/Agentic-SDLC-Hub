"use client";

import { useMemo, useState } from "react";
import Link from "next/link";

import { ProjectStatusBadge } from "@/components/status-badge";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState } from "@/components/ui/empty-state";
import { formatDate, formatStageLabel } from "@/lib/format";
import type { Project, ProjectStatus } from "@/lib/types";
import { FolderKanban } from "lucide-react";

const STATUS_FILTERS: Array<{ label: string; value: ProjectStatus | "ALL" }> = [
  { label: "All statuses", value: "ALL" },
  { label: "Active", value: "ACTIVE" },
  { label: "Completed", value: "COMPLETED" },
  { label: "Archived", value: "ARCHIVED" },
];

export function ProjectsTable({ projects }: { projects: Project[] }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<ProjectStatus | "ALL">("ALL");

  const filtered = useMemo(() => {
    return projects.filter((project) => {
      const matchesStatus = status === "ALL" || project.status === status;
      const matchesQuery =
        query.trim() === "" ||
        project.name.toLowerCase().includes(query.toLowerCase()) ||
        project.businessOwner.toLowerCase().includes(query.toLowerCase());
      return matchesStatus && matchesQuery;
    });
  }, [projects, query, status]);

  return (
    <div>
      <div className="mb-4 flex flex-col gap-2 sm:flex-row">
        <Input
          placeholder="Search by name or business owner…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="sm:max-w-xs"
        />
        <Select
          value={status}
          onChange={(e) => setStatus(e.target.value as ProjectStatus | "ALL")}
          className="sm:max-w-[160px]"
        >
          {STATUS_FILTERS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      </div>

      {filtered.length === 0 ? (
        <EmptyState icon={FolderKanban} title="No projects match your filters" description="Try clearing the search or status filter." />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Business owner</TableHead>
              <TableHead>Current stage</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Updated</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.map((project) => (
              <TableRow key={project.id}>
                <TableCell className="font-medium">
                  <Link href={`/projects/${project.id}`} className="hover:underline">
                    {project.name}
                  </Link>
                </TableCell>
                <TableCell className="text-muted-foreground">{project.businessOwner}</TableCell>
                <TableCell className="text-muted-foreground">{formatStageLabel(project.currentStage)}</TableCell>
                <TableCell>
                  <ProjectStatusBadge status={project.status} />
                </TableCell>
                <TableCell className="text-muted-foreground">{formatDate(project.updatedAt)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
