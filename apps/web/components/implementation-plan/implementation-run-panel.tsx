"use client";

import { useState } from "react";
import { CheckCircle2, ExternalLink, GitPullRequest, Loader2, PlayCircle, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { toImplementationRun } from "@/lib/mappers";
import type { ImplementationRunItem, ImplementationTaskArea } from "@/lib/types";

const SUPPORTED_AREAS: ImplementationTaskArea[] = ["BACKEND", "FRONTEND", "DATABASE", "DOCS"];

const CHANGE_TYPE_VARIANT: Record<string, "success" | "info" | "destructive"> = {
  create: "info",
  modify: "success",
  delete: "destructive",
};

/**
 * "Run Implementation Agent" + review (rule 6) — see
 * app/services/implementation_agent.py and app/models/implementation_run.py.
 * This only ever produces a proposed diff for a human to read; nothing
 * here — including Accept — ever writes to the repository or GitHub.
 */
export function ImplementationRunPanel({
  projectId,
  taskId,
  area,
  hasRepository,
  currentUserId,
}: {
  projectId: string;
  taskId: string;
  area: ImplementationTaskArea;
  hasRepository: boolean;
  currentUserId: string | null;
}) {
  const [run, setRun] = useState<ImplementationRunItem | null>(null);
  const [running, setRunning] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [creatingPr, setCreatingPr] = useState(false);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  const supported = SUPPORTED_AREAS.includes(area);

  async function handleRun() {
    if (!currentUserId) {
      setError("No users exist yet to attribute this run to.");
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const response = await api.implementationRuns.start({
        implementation_task_id: taskId,
        triggered_by_user_id: currentUserId,
      });
      setRun(toImplementationRun(response));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start the implementation agent.");
    } finally {
      setRunning(false);
    }
  }

  async function handleReview(decision: "ACCEPTED" | "REJECTED") {
    if (!run || !currentUserId) return;
    setReviewing(true);
    setError(null);
    try {
      const response = await api.implementationRuns.review(run.id, {
        decision,
        reviewed_by_user_id: currentUserId,
        comment: comment.trim() || null,
      });
      setRun(toImplementationRun(response));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to record the review decision.");
    } finally {
      setReviewing(false);
    }
  }

  async function handleCreatePullRequest() {
    if (!run || !currentUserId) return;
    setCreatingPr(true);
    setError(null);
    try {
      const response = await api.implementationRuns.createPullRequest(run.id, { triggered_by_user_id: currentUserId });
      setRun(toImplementationRun(response));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create the pull request.");
    } finally {
      setCreatingPr(false);
    }
  }

  if (!supported) {
    return (
      <p className="mt-2 border-t border-border pt-2 text-[11px] text-muted-foreground">
        No {area} Implementation Agent is available yet — only Backend, Frontend, Database, and Docs are implemented.
      </p>
    );
  }
  if (!hasRepository) {
    return (
      <p className="mt-2 border-t border-border pt-2 text-[11px] text-muted-foreground">
        Connect a GitHub repository and create a snapshot to run the Implementation Agent for this task.
      </p>
    );
  }

  return (
    <div className="mt-2 border-t border-border pt-2">
      {!run ? (
        <Button variant="outline" size="sm" className="h-7 gap-1.5 px-2 text-xs" onClick={handleRun} disabled={running}>
          {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
          {running ? "Running…" : "Run Implementation Agent"}
        </Button>
      ) : (
        <div className="flex flex-col gap-3 rounded-md border border-border bg-muted/30 p-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-1.5">
              <Badge variant={run.status === "COMPLETED" ? "success" : run.status === "FAILED" ? "destructive" : "gray"}>
                {run.status}
              </Badge>
              {run.status === "COMPLETED" ? (
                <Badge variant={run.reviewStatus === "ACCEPTED" ? "success" : run.reviewStatus === "REJECTED" ? "destructive" : "gray"}>
                  {run.reviewStatus}
                </Badge>
              ) : null}
              {run.usedMock ? <Badge variant="outline">heuristic scaffold — no real model configured</Badge> : null}
            </div>
            <Button variant="ghost" size="sm" className="h-6 px-2 text-[11px]" onClick={handleRun} disabled={running}>
              Run again
            </Button>
          </div>

          {run.status === "FAILED" ? (
            <p className="text-destructive">{run.errorMessage}</p>
          ) : (
            <>
              <p className="rounded bg-amber-100 px-2 py-1 text-[11px] text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                Preview only — no changes have been applied to the repository, and no commit or push has been made.
              </p>

              <div>
                <p className="mb-1 font-medium">Proposed file changes</p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Path</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Summary</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {run.proposedFileChanges.map((c) => (
                      <TableRow key={c.path}>
                        <TableCell className="whitespace-normal break-all font-mono text-[11px]">{c.path}</TableCell>
                        <TableCell>
                          <Badge variant={CHANGE_TYPE_VARIANT[c.changeType] ?? "outline"}>{c.changeType}</Badge>
                        </TableCell>
                        <TableCell className="whitespace-normal">{c.summary}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              <div>
                <p className="mb-1 font-medium">Diff</p>
                <pre className="max-h-72 overflow-auto rounded border border-border bg-background p-2 font-mono text-[11px] leading-relaxed">
                  {run.diffText || "(no diff produced)"}
                </pre>
              </div>

              <div>
                <p className="mb-1 font-medium">Explanation</p>
                <p className="whitespace-pre-line text-muted-foreground">{run.explanation}</p>
              </div>

              <div>
                <p className="mb-1 font-medium">Test command</p>
                <code className="rounded bg-background px-1.5 py-0.5 font-mono text-[11px]">{run.testCommand}</code>
              </div>

              {run.risks.length > 0 ? (
                <div>
                  <p className="mb-1 font-medium">Risks</p>
                  <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
                    {run.risks.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {run.status === "COMPLETED" && run.reviewStatus === "PENDING_REVIEW" ? (
                <div className="flex flex-col gap-2 border-t border-border pt-2">
                  <Textarea
                    placeholder="Optional review comment…"
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    className="min-h-[50px] text-xs"
                  />
                  <div className="flex gap-2">
                    <Button size="sm" className="h-7 gap-1.5 px-2 text-xs" onClick={() => handleReview("ACCEPTED")} disabled={reviewing}>
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      Accept
                    </Button>
                    <Button
                      variant="outline" size="sm" className="h-7 gap-1.5 px-2 text-xs"
                      onClick={() => handleReview("REJECTED")} disabled={reviewing}
                    >
                      <XCircle className="h-3.5 w-3.5" />
                      Reject
                    </Button>
                  </div>
                </div>
              ) : run.reviewedAt ? (
                <div className="flex flex-col gap-2 border-t border-border pt-2">
                  <p className="text-[11px] text-muted-foreground">
                    Reviewed {run.reviewStatus === "ACCEPTED" ? "— accepted" : "— rejected"}
                    {run.reviewComment ? `: "${run.reviewComment}"` : "."}
                  </p>
                  {run.reviewStatus === "ACCEPTED" ? (
                    run.pullRequest ? (
                      <div className="flex flex-wrap items-center gap-2 rounded border border-border bg-background p-2">
                        <Badge variant={run.pullRequest.status === "OPEN" ? "info" : run.pullRequest.status === "MERGED" ? "success" : "gray"}>
                          {run.pullRequest.status}
                        </Badge>
                        <a
                          href={run.pullRequest.prUrl} target="_blank" rel="noreferrer"
                          className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
                        >
                          <ExternalLink className="h-3 w-3" />
                          PR #{run.pullRequest.prNumber}
                        </a>
                        <code className="font-mono text-[10px] text-muted-foreground">{run.pullRequest.branchName}</code>
                      </div>
                    ) : (
                      <div>
                        <Button
                          size="sm" className="h-7 gap-1.5 px-2 text-xs"
                          onClick={handleCreatePullRequest} disabled={creatingPr}
                        >
                          {creatingPr ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitPullRequest className="h-3.5 w-3.5" />}
                          {creatingPr ? "Creating pull request…" : "Create PR"}
                        </Button>
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          This performs a real GitHub write: creates a new branch, commits the accepted changes onto it, and
                          opens a pull request. It never touches the repository&apos;s default branch.
                        </p>
                      </div>
                    )
                  ) : null}
                </div>
              ) : null}
            </>
          )}
        </div>
      )}
      {error ? <p className="mt-1 text-destructive">{error}</p> : null}
    </div>
  );
}
