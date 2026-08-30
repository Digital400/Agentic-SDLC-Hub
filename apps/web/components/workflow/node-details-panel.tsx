"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, PlayCircle, X } from "lucide-react";

import { ReviewStatusBadge, WorkflowStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { formatRelativeTime, formatSnakeCase } from "@/lib/format";
import { ApiError } from "@/lib/api";
import { runAgentAndApply } from "@/lib/run-agent";
import type { DocumentArtifact, ProjectWorkflowNode, ReviewItem } from "@/lib/types";

type AgentAction = "draft" | "improve" | "validate";

export function NodeDetailsPanel({
  projectId,
  node,
  documents,
  reviews,
  freeformInputKeys,
  currentUserId,
  onClose,
}: {
  projectId: string;
  node: ProjectWorkflowNode;
  documents: DocumentArtifact[];
  reviews: ReviewItem[];
  freeformInputKeys: string[];
  currentUserId: string | null;
  onClose: () => void;
}) {
  const router = useRouter();
  const artifact = documents.find((d) => d.projectId === projectId && d.artifactType === node.outputArtifactType);

  const review = reviews
    .filter((r) => r.projectId === projectId && r.workflowStageName === node.name)
    .sort((a, b) => (a.submittedAt < b.submittedAt ? 1 : -1))[0];

  const [action, setAction] = useState<AgentAction>("draft");
  const [freeformValues, setFreeformValues] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
        workflowNodeId: node.id,
        action,
        triggeredByUserId: currentUserId,
        inputContext: freeformValues,
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
    <aside className="flex max-h-[50vh] w-full shrink-0 flex-col overflow-y-auto rounded-lg border border-border bg-card lg:max-h-none lg:w-80">
      <div className="flex items-start justify-between gap-2 border-b border-border p-4">
        <div>
          <h2 className="text-sm font-semibold">{node.name}</h2>
          <div className="mt-1.5">
            <WorkflowStatusBadge status={node.status} />
          </div>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close panel" className="h-7 w-7 shrink-0">
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex flex-col gap-4 p-4 text-sm">
        <section>
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Description</h3>
          <p className="text-sm">{node.description}</p>
        </section>

        <Separator />

        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Required inputs</h3>
          {node.requiredInputs.length === 0 ? (
            <p className="text-xs text-muted-foreground">None — this is the first stage.</p>
          ) : (
            <div className="flex flex-wrap gap-1">
              {node.requiredInputs.map((input) => (
                <Badge key={input} variant="outline">
                  {formatSnakeCase(input)}
                </Badge>
              ))}
            </div>
          )}
        </section>

        <Separator />

        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Output artifact</h3>
          <p className="text-sm">{formatSnakeCase(node.outputArtifactType)}</p>
          {artifact ? (
            <div className="mt-2 rounded-md border border-border p-2">
              <p className="truncate text-xs font-medium">{artifact.title}</p>
              <p className="text-xs text-muted-foreground">
                v{artifact.versionNumber} · updated {formatRelativeTime(artifact.updatedAt)}
              </p>
              <Link
                href={`/documents/${artifact.id}`}
                className={buttonVariants({ variant: "outline", size: "sm", className: "mt-2 w-full" })}
              >
                Open artifact
              </Link>
            </div>
          ) : (
            <div className="mt-2 rounded-md border border-border p-2.5">
              <p className="mb-2 text-xs font-medium">Run {node.agentKey}</p>
              <Select value={action} onChange={(e) => setAction(e.target.value as AgentAction)} className="mb-2 text-xs">
                <option value="draft">Draft</option>
                <option value="improve">Improve</option>
                <option value="validate">Validate</option>
              </Select>
              {freeformInputKeys.map((key) => (
                <Textarea
                  key={key}
                  placeholder={key.replace(/_/g, " ")}
                  value={freeformValues[key] ?? ""}
                  onChange={(e) => setFreeformValues((prev) => ({ ...prev, [key]: e.target.value }))}
                  rows={2}
                  className="mb-2 text-xs"
                />
              ))}
              <Button size="sm" className="w-full" onClick={handleRun} disabled={running}>
                {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
                {running ? "Running…" : "Run Agent"}
              </Button>
              {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
            </div>
          )}
        </section>

        <Separator />

        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Allowed actions</h3>
          <div className="flex flex-wrap gap-1">
            {node.allowedActions.map((allowedAction) => (
              <Badge key={allowedAction} variant="secondary">
                {formatSnakeCase(allowedAction)}
              </Badge>
            ))}
          </div>
        </section>

        <Separator />

        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Review status</h3>
          {review ? (
            <div className="flex flex-col gap-1">
              <ReviewStatusBadge status={review.status} className="w-fit" />
              <p className="text-xs text-muted-foreground">
                {review.reviewerName} · submitted {formatRelativeTime(review.submittedAt)}
              </p>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              {node.requiresHumanApproval ? "No review submitted yet." : "This stage doesn't require human approval."}
            </p>
          )}
        </section>
      </div>
    </aside>
  );
}
