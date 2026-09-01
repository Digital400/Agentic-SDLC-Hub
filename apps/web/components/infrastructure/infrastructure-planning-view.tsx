"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, PlayCircle } from "lucide-react";

import { AgentRunStatusBadge, ReviewStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import { formatCost, formatRelativeTime, formatSnakeCase } from "@/lib/format";
import { runAgentAndApply } from "@/lib/run-agent";
import type { AgentRunDetail, DocumentArtifact, ProjectWorkflowNode, ReviewItem, ValidatorDefinitionItem } from "@/lib/types";

type AgentAction = "draft" | "improve" | "validate";

export function InfrastructurePlanningView({
  projectId,
  node,
  documents,
  reviews,
  freeformInputKeys,
  lastRun,
  validator,
  currentUserId,
  isAdmin,
}: {
  projectId: string;
  node: ProjectWorkflowNode | null;
  documents: DocumentArtifact[];
  reviews: ReviewItem[];
  freeformInputKeys: string[];
  lastRun: AgentRunDetail | null;
  validator: ValidatorDefinitionItem | null;
  currentUserId: string | null;
  isAdmin: boolean;
}) {
  const router = useRouter();
  const [action, setAction] = useState<AgentAction>("draft");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (node === null) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-muted-foreground">
          Infrastructure Planning isn&rsquo;t available for this project — it was created before this stage existed.
          Start a new project to use it.
        </CardContent>
      </Card>
    );
  }

  const artifact = documents.find((d) => d.projectId === projectId && d.artifactType === node.outputArtifactType);
  const review = reviews
    .filter((r) => r.projectId === projectId && r.workflowStageName === node.name)
    .sort((a, b) => (a.submittedAt < b.submittedAt ? 1 : -1))[0];
  const requiredArtifacts = node.requiredInputs.filter((input) => !freeformInputKeys.includes(input));

  async function handleRun() {
    if (currentUserId === null) {
      setError("No users exist yet to attribute this run to.");
      return;
    }
    setRunning(true);
    setError(null);
    try {
      const { run, saved } = await runAgentAndApply({
        projectId,
        workflowNodeId: node!.id,
        action,
        triggeredByUserId: currentUserId,
        inputContext: {},
      });
      if (run.status !== "COMPLETED" || saved === null) {
        setError(run.error_message ?? "The run did not complete.");
        return;
      }
      router.push(`/documents/${saved.artifactId}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to run the agent.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Description</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p>{node.description}</p>
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-muted-foreground">Assigned agent</span>
            <Badge variant="secondary">{node.agentKey}</Badge>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Required inputs</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {requiredArtifacts.length === 0 ? (
            <p className="text-xs text-muted-foreground">None.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {requiredArtifacts.map((input) => {
                const approved = documents.some((d) => d.projectId === projectId && d.artifactType === input && d.status === "APPROVED");
                return (
                  <Badge key={input} variant={approved ? "success" : "outline"}>
                    {formatSnakeCase(input)} — {approved ? "approved" : "not yet approved"}
                  </Badge>
                );
              })}
            </div>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            Rule: this stage can only run once every required input above is approved — no exceptions.
          </p>
        </CardContent>
      </Card>

      <Card className="md:col-span-2">
        <CardHeader>
          <CardTitle className="text-base">Infrastructure plan</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {artifact ? (
            <div className="flex items-center justify-between gap-2 rounded-md border border-border p-3">
              <div>
                <p className="font-medium">{artifact.title}</p>
                <p className="text-xs text-muted-foreground">
                  v{artifact.versionNumber} · {formatSnakeCase(artifact.status)} · updated {formatRelativeTime(artifact.updatedAt)}
                </p>
              </div>
              <Link href={`/documents/${artifact.id}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
                Open artifact
              </Link>
            </div>
          ) : (
            <div className="max-w-sm rounded-md border border-border p-3">
              <p className="mb-2 text-xs font-medium">Run {node.agentKey}</p>
              <Select value={action} onChange={(e) => setAction(e.target.value as AgentAction)} className="mb-2 text-xs">
                <option value="draft">Draft</option>
                <option value="improve">Improve</option>
                <option value="validate">Validate</option>
              </Select>
              <Button size="sm" onClick={handleRun} disabled={running}>
                {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
                {running ? "Running…" : "Run Agent"}
              </Button>
              {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Validator &amp; quality score</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {validator ? (
            <div className="space-y-1">
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="font-medium">{validator.name}</span>
                <span className="text-muted-foreground">threshold {Math.round(validator.qualityThreshold * 100)}%</span>
              </div>
              {lastRun?.loopQualityScore != null ? (
                <Badge variant={lastRun.loopQualityScore >= validator.qualityThreshold ? "success" : "warning"}>
                  {Math.round(lastRun.loopQualityScore * 100)}% quality
                </Badge>
              ) : (
                <p className="text-xs text-muted-foreground">No quality score yet.</p>
              )}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No validator configured for this stage.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Last agent run</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {lastRun ? (
            <div className="space-y-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5">
                  <AgentRunStatusBadge status={lastRun.status} />
                  <Badge variant="outline">{formatSnakeCase(lastRun.action)}</Badge>
                </div>
                <span className="text-muted-foreground">{formatRelativeTime(lastRun.createdAt)}</span>
              </div>
              <p className="text-muted-foreground">
                {lastRun.tokenUsage?.total_tokens ?? "—"} tokens · {formatCost(lastRun.cost)}
              </p>
              <div>
                <p className="mb-1 font-medium uppercase tracking-wide text-muted-foreground">RAG sources (cloud standards)</p>
                {lastRun.retrievedSourceTitles && lastRun.retrievedSourceTitles.length > 0 ? (
                  <div className="flex flex-wrap gap-1">
                    {lastRun.retrievedSourceTitles.map((title) => (
                      <Badge key={title} variant="secondary" className="max-w-full truncate">
                        {title}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-muted-foreground">No relevant sources found.</p>
                )}
              </div>
              {lastRun.errorMessage ? <p className="text-destructive">{lastRun.errorMessage}</p> : null}
              <Link href={`/agent-runs/${lastRun.id}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
                View full run
              </Link>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No agent run yet for this stage.</p>
          )}
        </CardContent>
      </Card>

      <Card className="md:col-span-2">
        <CardHeader>
          <CardTitle className="text-base">DevOps approval</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {review ? (
            <div className="flex items-center justify-between gap-2">
              <div>
                <ReviewStatusBadge status={review.status} className="w-fit" />
                <p className="mt-1 text-xs text-muted-foreground">
                  {review.reviewerName} · submitted {formatRelativeTime(review.submittedAt)}
                </p>
              </div>
              <Link href={`/reviews/${review.id}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
                Open review
              </Link>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              No review submitted yet. Rule: this plan is never deployed automatically — a DevOps approval is
              required before it&rsquo;s considered final.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
