"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, FolderTree, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import { toRepoContextPreview } from "@/lib/mappers";
import type { RepoContextPreviewItem } from "@/lib/types";

const CONTENT_MODE_VARIANT: Record<RepoContextPreviewItem["relevantFiles"][number]["contentMode"], "success" | "gray" | "warning"> = {
  full: "success",
  summary: "gray",
  omitted: "warning",
};

/**
 * "Repo Context Preview" (rule 8) — shows exactly what repo context would
 * be sent to a coding agent for this task before any coding agent exists
 * to consume it. See app/services/repo_context_builder.py: the whole
 * repository is never in scope — only what's shown here would ever reach
 * an agent's prompt.
 */
export function RepoContextPreviewPanel({ projectId, taskId }: { projectId: string; taskId: string }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<RepoContextPreviewItem | null>(null);

  async function handleToggle() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (preview) return; // already fetched — don't refetch on every re-open
    setLoading(true);
    setError(null);
    try {
      const response = await api.projects.repoContextPreview(projectId, taskId);
      setPreview(toRepoContextPreview(response));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to build the repo context preview.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mt-2 border-t border-border pt-2">
      <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2 text-xs" onClick={handleToggle}>
        <FolderTree className="h-3.5 w-3.5" />
        Preview Repo Context
        {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      </Button>

      {open ? (
        <div className="mt-2 flex flex-col gap-3 rounded-md border border-border bg-muted/30 p-3 text-xs">
          {loading ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Building the repo context preview…
            </div>
          ) : error ? (
            <p className="text-destructive">{error}</p>
          ) : preview ? (
            <>
              <div>
                <p className="mb-1 font-medium">Architecture summary</p>
                <p className="whitespace-pre-line text-muted-foreground">{preview.architectureSummary}</p>
              </div>

              <div>
                <p className="mb-1 font-medium">
                  Relevant folders ({preview.relevantFolders.length}) — {preview.filesIncluded} of{" "}
                  {preview.filesConsidered} scanned files included
                </p>
                <div className="flex flex-wrap gap-1">
                  {preview.relevantFolders.length === 0 ? (
                    <span className="text-muted-foreground">None found.</span>
                  ) : (
                    preview.relevantFolders.map((f) => (
                      <Badge key={f} variant="outline" className="font-mono text-[10px]">
                        {f}
                      </Badge>
                    ))
                  )}
                </div>
              </div>

              <div>
                <p className="mb-1 font-medium">Relevant files</p>
                {preview.relevantFiles.length === 0 ? (
                  <p className="text-muted-foreground">No files scored as relevant to this task.</p>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Path</TableHead>
                        <TableHead>Size</TableHead>
                        <TableHead>Score</TableHead>
                        <TableHead>Content</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {preview.relevantFiles.map((f) => (
                        <TableRow key={f.path}>
                          <TableCell className="whitespace-normal break-all font-mono text-[11px]">{f.path}</TableCell>
                          <TableCell>{f.size ?? "—"}</TableCell>
                          <TableCell>{f.score}</TableCell>
                          <TableCell>
                            <Badge variant={CONTENT_MODE_VARIANT[f.contentMode]}>{f.contentMode}</Badge>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </div>

              <div>
                <p className="mb-1 font-medium">Suggested edit scope</p>
                <div className="flex flex-wrap gap-1">
                  {preview.suggestedEditScope.length === 0 ? (
                    <span className="text-muted-foreground">None declared.</span>
                  ) : (
                    preview.suggestedEditScope.map((e) => (
                      <Badge key={e.path} variant={e.status === "new" ? "info" : "outline"} className="font-mono text-[10px]">
                        {e.status === "new" ? "new · " : ""}
                        {e.path}
                      </Badge>
                    ))
                  )}
                </div>
              </div>

              {preview.dependencyNotes.length > 0 ? (
                <div>
                  <p className="mb-1 font-medium">Dependency notes</p>
                  <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
                    {preview.dependencyNotes.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div>
                <p className="mb-1 font-medium">
                  Token budget — {preview.tokenBudgetReport.estimatedTokens} / {preview.tokenBudgetReport.contextTokenBudget}
                  {preview.tokenBudgetReport.overBudget ? (
                    <Badge variant="warning" className="ml-1.5">
                      over budget, compressed/dropped
                    </Badge>
                  ) : null}
                </p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Block</TableHead>
                      <TableHead>Priority</TableHead>
                      <TableHead>Tokens</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {preview.tokenBudgetReport.blocks.map((b) => (
                      <TableRow key={b.label}>
                        <TableCell className="whitespace-normal break-all font-mono text-[11px]">{b.label}</TableCell>
                        <TableCell>{b.priority}</TableCell>
                        <TableCell>{b.estimatedTokens}</TableCell>
                        <TableCell>
                          {!b.included ? (
                            <Badge variant="warning">dropped</Badge>
                          ) : b.truncated ? (
                            <Badge variant="gray">truncated</Badge>
                          ) : (
                            <Badge variant="success">included</Badge>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
