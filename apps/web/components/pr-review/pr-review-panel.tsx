"use client";

import { useState } from "react";
import Link from "next/link";
import { ExternalLink, Loader2, PlayCircle, Send } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { toPRReviewRun } from "@/lib/mappers";
import type { PRReviewRunItem } from "@/lib/types";

const RECOMMENDATION_VARIANT: Record<string, "success" | "destructive" | "gray"> = {
  APPROVE: "success",
  REQUEST_CHANGES: "destructive",
  COMMENT_ONLY: "gray",
};

/**
 * "Run PR Review Agent" + "post selected comments to GitHub" — see
 * app/services/pr_review_agent.py and app/api/routes/pr_review_runs.py.
 * The agent never approves anything and never merges — the human
 * reviewer decides final approval on the real GitHub PR.
 */
export function PRReviewPanel({ taskId, currentUserId }: { taskId: string; currentUserId: string | null }) {
  const [run, setRun] = useState<PRReviewRunItem | null>(null);
  const [running, setRunning] = useState(false);
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Editable draft text + selection, keyed by index into suggestedComments —
  // requirement: "agent comments must be editable before posting."
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [selected, setSelected] = useState<Record<number, boolean>>({});

  async function handleRun() {
    if (!currentUserId) {
      setError("No users exist yet to attribute this run to.");
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const response = await api.prReviewRuns.start({ implementation_task_id: taskId, triggered_by_user_id: currentUserId });
      const mapped = toPRReviewRun(response);
      setRun(mapped);
      setDrafts(Object.fromEntries(mapped.suggestedComments.map((c, i) => [i, c.body])));
      setSelected({});
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start the PR Review Agent.");
    } finally {
      setRunning(false);
    }
  }

  async function handlePostSelected() {
    if (!run || !currentUserId) return;
    const toPost = run.suggestedComments
      .map((c, i) => ({ file: c.file, body: drafts[i] ?? c.body, selected: selected[i] }))
      .filter((c) => c.selected && c.body.trim());
    if (toPost.length === 0) {
      setError("Select at least one comment to post.");
      return;
    }
    setPosting(true);
    setError(null);
    try {
      const response = await api.prReviewRuns.postComments(run.id, {
        triggered_by_user_id: currentUserId,
        comments: toPost.map(({ file, body }) => ({ file, body })),
      });
      setRun(toPRReviewRun(response.run));
      const failed = response.results.filter((r) => r.status === "failed");
      if (failed.length > 0) setError(`${failed.length} comment(s) failed to post.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to post comments.");
    } finally {
      setPosting(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border border-border p-3 text-xs">
      {!run ? (
        <Button size="sm" className="h-7 w-fit gap-1.5 px-2 text-xs" onClick={handleRun} disabled={running}>
          {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
          {running ? "Running…" : "Run PR Review Agent"}
        </Button>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant={run.status === "COMPLETED" ? "success" : run.status === "FAILED" ? "destructive" : "gray"}>
              {run.status}
            </Badge>
            {run.overallRecommendation ? (
              <Badge variant={RECOMMENDATION_VARIANT[run.overallRecommendation] ?? "outline"}>{run.overallRecommendation}</Badge>
            ) : null}
            {run.riskScore !== null ? <Badge variant="outline">Risk: {run.riskScore}/100</Badge> : null}
            {run.usedMock ? <Badge variant="outline">heuristic — no real model configured</Badge> : null}
          </div>

          {run.status === "FAILED" ? (
            <p className="text-destructive">{run.errorMessage}</p>
          ) : (
            <>
              <p className="text-muted-foreground">{run.summary}</p>

              {(["criticalFindings", "majorFindings", "minorFindings"] as const).map((key) => {
                const items = run[key];
                if (items.length === 0) return null;
                const label = key === "criticalFindings" ? "Critical" : key === "majorFindings" ? "Major" : "Minor";
                return (
                  <div key={key}>
                    <p className="mb-1 font-medium">{label} findings</p>
                    <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
                      {items.map((f, i) => (
                        <li key={`${key}-${i}`}>
                          <span className="font-mono">{f.file}</span> — {f.detail}
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}

              {run.missingTests.length > 0 ? (
                <div>
                  <p className="mb-1 font-medium">Missing tests</p>
                  <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
                    {run.missingTests.map((m) => (
                      <li key={m}>{m}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {run.suggestedComments.length > 0 ? (
                <div>
                  <p className="mb-1 font-medium">Suggested comments — edit before posting</p>
                  <div className="flex flex-col gap-2">
                    {run.suggestedComments.map((c, i) => (
                      <div key={i} className="flex items-start gap-2 rounded border border-border p-2">
                        <Checkbox
                          checked={!!selected[i]}
                          onChange={(e) => setSelected((s) => ({ ...s, [i]: e.target.checked }))}
                          className="mt-1"
                        />
                        <div className="flex-1">
                          <p className="mb-1 font-mono text-[10px] text-muted-foreground">{c.file}</p>
                          <Textarea
                            value={drafts[i] ?? c.body}
                            onChange={(e) => setDrafts((d) => ({ ...d, [i]: e.target.value }))}
                            className="min-h-[50px] text-xs"
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                  <Button size="sm" className="mt-2 h-7 gap-1.5 px-2 text-xs" onClick={handlePostSelected} disabled={posting}>
                    {posting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
                    {posting ? "Posting…" : "Post selected comments to GitHub"}
                  </Button>
                </div>
              ) : null}

              {run.postedComments.length > 0 ? (
                <div>
                  <p className="mb-1 font-medium">Posted comments</p>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>File</TableHead>
                        <TableHead>Link</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {run.postedComments.map((c) => (
                        <TableRow key={c.githubCommentId}>
                          <TableCell className="font-mono text-[11px]">{c.file || "(general)"}</TableCell>
                          <TableCell>
                            <Link href={c.githubCommentUrl} target="_blank" className="inline-flex items-center gap-1 text-primary hover:underline">
                              <ExternalLink className="h-3 w-3" />
                              View on GitHub
                            </Link>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : null}

              <p className="rounded bg-amber-100 px-2 py-1 text-[11px] text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                {run.finalReviewerNote}
              </p>
            </>
          )}
        </>
      )}
      {error ? <p className="text-destructive">{error}</p> : null}
    </div>
  );
}
