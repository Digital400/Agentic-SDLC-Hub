"use client";

import { useState } from "react";
import Link from "next/link";
import { ClipboardList, Loader2, PlayCircle } from "lucide-react";

import { AgentRunStatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { formatRelativeTime } from "@/lib/format";
import { toMaintenanceRun } from "@/lib/mappers";
import type { MaintenanceRunItem } from "@/lib/types";

/**
 * "Generate weekly maintenance report" (requirement 6 — manual, repeatable
 * any time, not just weekly) — see app/services/maintenance_agent.py.
 * Rule: the agent cannot change production and only ever recommends
 * actions, so there is no approval step here — a completed report opens
 * straight in the document viewer, already APPROVED.
 */
export function MaintenanceView({
  projectId,
  initialRuns,
  currentUserId,
}: {
  projectId: string;
  initialRuns: MaintenanceRunItem[];
  currentUserId: string | null;
}) {
  const [runs, setRuns] = useState(initialRuns);
  const [errorLogs, setErrorLogs] = useState("");
  const [userFeedback, setUserFeedback] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleGenerate() {
    if (currentUserId === null) {
      setError("No users exist yet to attribute this run to.");
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const response = await api.maintenanceRuns.start({
        project_id: projectId,
        triggered_by_user_id: currentUserId,
        error_logs: errorLogs.trim() || null,
        user_feedback: userFeedback.trim() || null,
      });
      setRuns((prev) => [toMaintenanceRun(response), ...prev]);
      setErrorLogs("");
      setUserFeedback("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to generate a maintenance report.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Generate a maintenance report</CardTitle>
          <CardDescription>
            Pulls the released project summary, open bugs, PR history, and test results automatically. Error
            logs and user feedback below are optional — leave them blank if none are available.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Error logs (optional)</label>
            <Textarea
              value={errorLogs}
              onChange={(e) => setErrorLogs(e.target.value)}
              placeholder="Paste any relevant error logs, if available…"
              rows={3}
              className="text-xs"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">User feedback (optional)</label>
            <Textarea
              value={userFeedback}
              onChange={(e) => setUserFeedback(e.target.value)}
              placeholder="Paste any relevant user feedback, if available…"
              rows={3}
              className="text-xs"
            />
          </div>
          <Button size="sm" onClick={handleGenerate} disabled={running} className="w-fit">
            {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
            {running ? "Generating…" : "Generate report"}
          </Button>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Report history</CardTitle>
        </CardHeader>
        <CardContent>
          {runs.length === 0 ? (
            <EmptyState
              icon={ClipboardList}
              title="No maintenance reports yet"
              description="Generate one above once this project has an approved release."
              className="py-6"
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Status</TableHead>
                  <TableHead>Generated</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell>
                      <AgentRunStatusBadge status={run.status} />
                      {run.errorMessage ? <p className="mt-1 text-xs text-destructive">{run.errorMessage}</p> : null}
                    </TableCell>
                    <TableCell>{formatRelativeTime(run.createdAt)}</TableCell>
                    <TableCell>
                      {run.status === "COMPLETED" && run.artifactId ? (
                        <Link href={`/documents/${run.artifactId}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
                          Open report
                        </Link>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
