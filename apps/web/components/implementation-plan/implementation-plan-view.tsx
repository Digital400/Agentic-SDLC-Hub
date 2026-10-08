"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ExternalLink, ListChecks, Loader2, Sparkles } from "lucide-react";

import { ImplementationRunPanel } from "@/components/implementation-plan/implementation-run-panel";
import { RepoContextPreviewPanel } from "@/components/implementation-plan/repo-context-preview-panel";
import { ImplementationTaskRiskBadge, ImplementationTaskStatusBadge, WorkflowStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Select } from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import { toGenerateImplementationPlanResult } from "@/lib/mappers";
import type { GithubRepositoryItem, ImplementationTaskArea, ImplementationTaskItem, ProjectWorkflowNode } from "@/lib/types";

const AREA_ORDER: ImplementationTaskArea[] = ["BACKEND", "FRONTEND", "DATABASE", "TESTING", "INFRA", "DOCS"];
const AREA_LABEL: Record<ImplementationTaskArea, string> = {
  BACKEND: "Backend",
  FRONTEND: "Frontend",
  DATABASE: "Database",
  TESTING: "Testing",
  INFRA: "Infrastructure",
  DOCS: "Docs",
};

export function ImplementationPlanView({
  projectId,
  node,
  tasks,
  pendingReviewId,
  currentUserId,
  reviewers,
  hasRepository,
  repositories,
}: {
  projectId: string;
  /** Null for a project created before this stage existed — see the
   * workflow template's "new projects only" limitation. */
  node: ProjectWorkflowNode | null;
  tasks: ImplementationTaskItem[];
  pendingReviewId: string | null;
  currentUserId: string | null;
  reviewers: { id: string; name: string }[];
  /** Whether this project has a configured GitHub repository — gates the
   * per-task "Preview Repo Context" panel (see repo-context-preview-panel.tsx),
   * which needs a repository snapshot to build anything from. */
  hasRepository: boolean;
  /** Multi-repo support — every repository connected to this project. The
   * per-task repository picker below only renders once there's more than
   * one to choose from; a project with just one repo behaves exactly as
   * before (every task silently uses that repo). */
  repositories: GithubRepositoryItem[];
}) {
  const router = useRouter();
  const [reviewerId, setReviewerId] = useState(
    reviewers.find((r) => r.id !== currentUserId)?.id ?? reviewers[0]?.id ?? ""
  );
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<{ taskCount: number; reviewId: string } | null>(null);
  // Multi-repo support — optimistic local override so the picker reflects
  // a just-made assignment immediately, without waiting on router.refresh().
  const [taskRepositoryOverrides, setTaskRepositoryOverrides] = useState<Record<string, string | null>>({});
  const [repositoryAssignError, setRepositoryAssignError] = useState<string | null>(null);

  async function handleAssignRepository(taskId: string, repositoryId: string) {
    const resolved = repositoryId || null;
    setRepositoryAssignError(null);
    try {
      await api.implementationTasks.updateRepository(taskId, resolved);
      setTaskRepositoryOverrides((prev) => ({ ...prev, [taskId]: resolved }));
    } catch (err) {
      setRepositoryAssignError(err instanceof ApiError ? err.message : "Failed to assign a repository to this task.");
    }
  }

  async function handleGenerate() {
    if (!node) return;
    if (currentUserId === null) {
      setError("No users exist yet to attribute this generation to.");
      return;
    }
    if (!reviewerId) {
      setError("No reviewer available to select.");
      return;
    }
    setGenerating(true);
    setError(null);
    try {
      const response = await api.projects.generateImplementationPlan(projectId, {
        triggered_by_user_id: currentUserId,
        reviewer_id: reviewerId,
      });
      const result = toGenerateImplementationPlanResult(response);
      setLastResult({ taskCount: result.tasks.length, reviewId: result.reviewId });
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to generate the implementation plan.");
    } finally {
      setGenerating(false);
    }
  }

  if (!node) {
    return (
      <EmptyState
        icon={ListChecks}
        title="Implementation Planning isn't part of this project's workflow"
        description="This project was created before the Implementation Planning stage existed — new projects include it between LLD and Implementation."
      />
    );
  }

  const isLocked = node.status === "LOCKED";
  const canGenerate = !isLocked && !pendingReviewId && !generating;
  const openReviewId = pendingReviewId ?? lastResult?.reviewId ?? null;

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="text-base">Implementation Planning</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              Generates a structured task breakdown from the approved LLD — Tech Lead review required before
              Implementation can start.
            </p>
          </div>
          <WorkflowStatusBadge status={node.status} />
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {isLocked ? (
            <p className="text-xs text-muted-foreground">
              Locked until the LLD and its story backlog are approved.
            </p>
          ) : openReviewId ? (
            <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-muted/40 p-3">
              <p className="text-xs text-muted-foreground">
                A review is pending for this plan — approving it is what unlocks Implementation.
              </p>
              <Link href={`/reviews/${openReviewId}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
                <ExternalLink className="h-3.5 w-3.5" />
                Open review
              </Link>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Select value={reviewerId} onChange={(e) => setReviewerId(e.target.value)} className="w-56 text-xs">
                {reviewers.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </Select>
              <Button size="sm" onClick={handleGenerate} disabled={!canGenerate}>
                {generating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                {generating ? "Generating…" : "Generate Implementation Plan"}
              </Button>
            </div>
          )}
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
        </CardContent>
      </Card>

      {repositoryAssignError ? <p className="text-xs text-destructive">{repositoryAssignError}</p> : null}

      {tasks.length === 0 ? (
        <EmptyState
          icon={ListChecks}
          title="No implementation tasks yet"
          description="Generate a plan once the LLD is approved — tasks will appear here grouped by area."
        />
      ) : (
        AREA_ORDER.map((area) => {
          const areaTasks = tasks.filter((t) => t.area === area);
          if (areaTasks.length === 0) return null;
          return (
            <Card key={area}>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle className="text-sm">{AREA_LABEL[area]}</CardTitle>
                <Badge variant="outline">{areaTasks.length} task{areaTasks.length === 1 ? "" : "s"}</Badge>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                {areaTasks.map((task) => (
                  <div key={task.id} className="rounded-md border border-border p-3">
                    <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                      <h3 className="text-sm font-medium">{task.title}</h3>
                      <div className="flex items-center gap-1.5">
                        <ImplementationTaskRiskBadge riskLevel={task.riskLevel} />
                        <ImplementationTaskStatusBadge status={task.status} />
                      </div>
                    </div>
                    <p className="mb-2 text-xs text-muted-foreground">{task.description}</p>
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                      <div>
                        <dt className="text-muted-foreground">Linked story</dt>
                        <dd>{task.linkedStory ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Linked LLD section</dt>
                        <dd>{task.linkedLldSection ?? "—"}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Assigned agent type</dt>
                        <dd>{task.assignedAgentType}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Test expectation</dt>
                        <dd className="truncate">{task.testExpectation || "—"}</dd>
                      </div>
                      {task.expectedPaths.length > 0 ? (
                        <div className="col-span-2">
                          <dt className="text-muted-foreground">Expected files/folders</dt>
                          <dd className="flex flex-wrap gap-1 pt-0.5">
                            {task.expectedPaths.map((p) => (
                              <Badge key={p} variant="outline" className="font-mono text-[10px]">
                                {p}
                              </Badge>
                            ))}
                          </dd>
                        </div>
                      ) : null}
                      {task.dependencies.length > 0 ? (
                        <div className="col-span-2">
                          <dt className="text-muted-foreground">Dependencies</dt>
                          <dd className="flex flex-wrap gap-1 pt-0.5">
                            {task.dependencies.map((d) => (
                              <Badge key={d} variant="secondary">
                                {d}
                              </Badge>
                            ))}
                          </dd>
                        </div>
                      ) : null}
                      {task.acceptanceCriteria.length > 0 ? (
                        <div className="col-span-2">
                          <dt className="mb-0.5 text-muted-foreground">Acceptance criteria</dt>
                          <dd>
                            <ul className="list-inside list-disc space-y-0.5">
                              {task.acceptanceCriteria.map((c) => (
                                <li key={c}>{c}</li>
                              ))}
                            </ul>
                          </dd>
                        </div>
                      ) : null}
                    </dl>
                    {repositories.length > 1 ? (
                      <div className="mt-2 border-t border-border pt-2">
                        <label className="mb-1 block text-[11px] text-muted-foreground">
                          Target repository (defaults to the project&rsquo;s primary repository)
                        </label>
                        <Select
                          value={taskRepositoryOverrides[task.id] ?? task.repositoryId ?? ""}
                          onChange={(e) => handleAssignRepository(task.id, e.target.value)}
                          className="w-72 text-xs"
                        >
                          <option value="">Use primary repository</option>
                          {repositories.map((r) => (
                            <option key={r.id} value={r.id}>
                              {r.owner}/{r.name}
                              {r.isPrimary ? " (primary)" : ""}
                            </option>
                          ))}
                        </Select>
                      </div>
                    ) : null}
                    {hasRepository ? (
                      <RepoContextPreviewPanel projectId={projectId} taskId={task.id} />
                    ) : (
                      <p className="mt-2 border-t border-border pt-2 text-[11px] text-muted-foreground">
                        Connect a GitHub repository (Settings → Integrations → GitHub) to preview repo context for this task.
                      </p>
                    )}
                    <ImplementationRunPanel
                      projectId={projectId}
                      taskId={task.id}
                      area={task.area}
                      hasRepository={hasRepository}
                      currentUserId={currentUserId}
                    />
                  </div>
                ))}
              </CardContent>
            </Card>
          );
        })
      )}
    </div>
  );
}
