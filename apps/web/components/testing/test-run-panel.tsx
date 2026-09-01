"use client";

import { useState } from "react";
import Link from "next/link";
import { ExternalLink, Loader2, PlayCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import { toTestRun } from "@/lib/mappers";
import type { TestAgentType, TestRunItem } from "@/lib/types";

const AGENT_TYPES: { value: TestAgentType; label: string }[] = [
  { value: "UNIT", label: "Unit Test Agent" },
  { value: "API", label: "API Test Agent" },
  { value: "UI", label: "UI Test Agent" },
  { value: "REGRESSION", label: "Regression Test Agent" },
  { value: "SECURITY", label: "Security Test Agent" },
];

const RESULT_VARIANT: Record<string, "success" | "destructive"> = { PASS: "success", FAIL: "destructive" };

/**
 * "Run Testing Agent" (rule: one of five agent types) — see
 * app/services/testing_agent.py. Completing a run opens a real QA Review
 * at the existing /reviews/{id} screen — approval never happens here,
 * and the agent never approves its own result (see run.reviewId below).
 */
export function TestRunPanel({
  taskId,
  currentUserId,
  reviewers,
}: {
  taskId: string;
  currentUserId: string | null;
  reviewers: { id: string; name: string }[];
}) {
  const [agentType, setAgentType] = useState<TestAgentType>("UNIT");
  const [reviewerId, setReviewerId] = useState(reviewers[0]?.id ?? "");
  const [run, setRun] = useState<TestRunItem | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRun() {
    if (!currentUserId || !reviewerId) {
      setError("A triggering user and a QA reviewer are both required.");
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const response = await api.testRuns.start({
        implementation_task_id: taskId,
        agent_type: agentType,
        triggered_by_user_id: currentUserId,
        reviewer_id: reviewerId,
      });
      setRun(toTestRun(response));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start the testing agent.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border border-border p-3 text-xs">
      {!run ? (
        <div className="flex flex-wrap items-center gap-2">
          <Select value={agentType} onChange={(e) => setAgentType(e.target.value as TestAgentType)} className="w-48 text-xs">
            {AGENT_TYPES.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </Select>
          <Select value={reviewerId} onChange={(e) => setReviewerId(e.target.value)} className="w-48 text-xs">
            {reviewers.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </Select>
          <Button size="sm" className="h-7 gap-1.5 px-2 text-xs" onClick={handleRun} disabled={running}>
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
            {running ? "Running…" : "Run Testing Agent"}
          </Button>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant={run.status === "COMPLETED" ? "success" : run.status === "FAILED" ? "destructive" : "gray"}>
              {run.status}
            </Badge>
            <Badge variant="outline">{run.agentType} Test Agent</Badge>
            {run.usedMock ? <Badge variant="outline">heuristic — no real model configured</Badge> : null}
          </div>

          {run.status === "FAILED" ? (
            <p className="text-destructive">{run.errorMessage}</p>
          ) : (
            <>
              <div>
                <p className="mb-1 font-medium">Test plan</p>
                <p className="whitespace-pre-line text-muted-foreground">{run.testPlan || "(none produced)"}</p>
              </div>

              {run.testsToAdd.length > 0 ? (
                <div>
                  <p className="mb-1 font-medium">Tests to add</p>
                  <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
                    {run.testsToAdd.map((t) => (
                      <li key={t.name}>
                        <span className="font-mono">{t.name}</span> — {t.description}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div>
                <p className="mb-1 font-medium">
                  Tests executed — Pass: {run.passCount} · Fail: {run.failCount}
                </p>
                <p className="mb-1 rounded bg-amber-100 px-2 py-1 text-[11px] text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                  Reasoning-based assessment from the diff — this codebase has no test-execution sandbox, so this is
                  never a real CI run.
                </p>
                {run.testsExecuted.length > 0 ? (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Test</TableHead>
                        <TableHead>Result</TableHead>
                        <TableHead>Notes</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {run.testsExecuted.map((t) => (
                        <TableRow key={t.name}>
                          <TableCell className="font-mono text-[11px]">{t.name}</TableCell>
                          <TableCell>
                            <Badge variant={RESULT_VARIANT[t.result] ?? "outline"}>{t.result}</Badge>
                          </TableCell>
                          <TableCell className="whitespace-normal">{t.notes}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <p className="text-muted-foreground">No test execution assessment was produced.</p>
                )}
              </div>

              {run.bugsFound.length > 0 ? (
                <div>
                  <p className="mb-1 font-medium">Bugs found</p>
                  <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
                    {run.bugsFound.map((b) => (
                      <li key={b}>{b}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {run.suggestedFixes.length > 0 ? (
                <div>
                  <p className="mb-1 font-medium">Suggested fixes</p>
                  <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
                    {run.suggestedFixes.map((f) => (
                      <li key={f}>{f}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div>
                <p className="mb-1 font-medium">Coverage impact</p>
                <p className="text-muted-foreground">{run.coverageImpact.note ?? "(placeholder — no coverage tooling wired up yet)"}</p>
              </div>

              {run.reviewId ? (
                <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/40 p-2">
                  <p className="text-muted-foreground">A QA review is pending — approval happens there, not here.</p>
                  <Link href={`/reviews/${run.reviewId}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
                    <ExternalLink className="h-3.5 w-3.5" />
                    Open review
                  </Link>
                </div>
              ) : null}
            </>
          )}
        </>
      )}
      {error ? <p className="text-destructive">{error}</p> : null}
    </div>
  );
}
