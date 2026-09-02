"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AlertTriangle, Loader2, PlayCircle, ShieldAlert, X } from "lucide-react";
import { WORKFLOW_STATUSES, type WorkflowStatus } from "@agentic-sdlc-hub/shared";

import { AgentRunStatusBadge, ReviewStatusBadge, WorkflowStatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { formatCost, formatRelativeTime, formatSnakeCase } from "@/lib/format";
import { api, ApiError } from "@/lib/api";
import { runAgentAndApply } from "@/lib/run-agent";
import type { AgentRunDetail, DocumentArtifact, ProjectWorkflowNode, ReviewItem, ValidatorDefinitionItem } from "@/lib/types";

type AgentAction = "draft" | "improve" | "validate";

// Scrum story lanes, requirement 2/3/4 — mirrors
// app/db/seed.py's story_crafting system prompt.
const STORY_CRAFTING_MODE_HELPER_TEXT: Record<"VERTICAL" | "HORIZONTAL", string> = {
  VERTICAL: "Creates end-to-end user value stories suitable for Scrum sprint delivery.",
  HORIZONTAL: "Creates technical layer stories such as frontend, backend, database, integration, infra, testing, or documentation.",
};

export function NodeDetailsPanel({
  projectId,
  node,
  documents,
  reviews,
  freeformInputKeys,
  lastRun,
  validator,
  currentUserId,
  isAdmin,
  onClose,
}: {
  projectId: string;
  node: ProjectWorkflowNode;
  documents: DocumentArtifact[];
  reviews: ReviewItem[];
  freeformInputKeys: string[];
  /** This node's most recent agent run, if any — backs quality score,
   * last run, token usage, and RAG sources. */
  lastRun: AgentRunDetail | null;
  /** This stage's independent quality validator, if one is configured. */
  validator: ValidatorDefinitionItem | null;
  currentUserId: string | null;
  isAdmin: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const artifact = documents.find((d) => d.projectId === projectId && d.artifactType === node.outputArtifactType);

  const review = reviews
    .filter((r) => r.projectId === projectId && r.workflowStageName === node.name)
    .sort((a, b) => (a.submittedAt < b.submittedAt ? 1 : -1))[0];

  // A required input that matches some other node's output artifact type is
  // an upstream artifact dependency; anything else is freeform (typed in
  // directly, e.g. a stakeholder request) — see freeformInputKeys.
  const requiredArtifacts = node.requiredInputs.filter((input) => !freeformInputKeys.includes(input));

  const [action, setAction] = useState<AgentAction>("draft");
  const [freeformValues, setFreeformValues] = useState<Record<string, string>>({});
  // Scrum story lanes, requirement 2 — the one deliberately special-cased
  // extra control on this stage (see app/db/seed.py's story_crafting
  // system prompt for what VERTICAL/HORIZONTAL each mean). Not a freeform
  // input: it has a real default (VERTICAL) rather than being required.
  const [storyCraftingMode, setStoryCraftingMode] = useState<"VERTICAL" | "HORIZONTAL">("VERTICAL");
  // Optional inputs the story-crafting-agent prompt reads if given — see
  // app/db/seed.py. Blank means "not provided," not an empty string sent
  // to the agent (see handleRun's inputContext below).
  const [sprintGoal, setSprintGoal] = useState("");
  const [teamCapacity, setTeamCapacity] = useState("");
  const isStoryCrafting = node.nodeKey === "story_crafting";
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [overrideStatus, setOverrideStatus] = useState<WorkflowStatus>(node.status);
  const [overrideReason, setOverrideReason] = useState("");
  const [overriding, setOverriding] = useState(false);
  const [overrideError, setOverrideError] = useState<string | null>(null);
  const [overrideDone, setOverrideDone] = useState(false);

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
        inputContext: isStoryCrafting
          ? {
              ...freeformValues,
              story_crafting_mode: storyCraftingMode,
              ...(sprintGoal.trim() ? { sprint_goal: sprintGoal.trim() } : {}),
              ...(teamCapacity.trim() ? { team_capacity: teamCapacity.trim() } : {}),
            }
          : freeformValues,
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

  async function handleOverride() {
    if (currentUserId === null) {
      setOverrideError("No users exist yet to attribute this override to.");
      return;
    }
    if (!overrideReason.trim()) {
      setOverrideError("A reason is required for a manual override.");
      return;
    }
    setOverriding(true);
    setOverrideError(null);
    try {
      await api.projects.updateNodeStatus(projectId, node.id, {
        status: overrideStatus,
        reason: overrideReason.trim(),
        overridden_by_id: currentUserId,
      });
      setOverrideDone(true);
      router.refresh();
    } catch (err) {
      setOverrideError(err instanceof ApiError ? err.message : "Failed to override this node's status.");
    } finally {
      setOverriding(false);
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
        {/* 10. Blocked reason — surfaced first since it explains everything else. */}
        {node.status === "BLOCKED" ? (
          <div className="flex items-start gap-2 rounded-md border border-purple-300 bg-purple-50 p-2.5 text-xs text-purple-900 dark:border-purple-900 dark:bg-purple-950 dark:text-purple-200">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{node.blockedReason ?? "This node is blocked, but no reason was recorded."}</span>
          </div>
        ) : null}

        {node.overrideReason ? (
          <p className="text-xs italic text-muted-foreground">
            Current status was set by a manual override: “{node.overrideReason}”
          </p>
        ) : null}

        <section>
          <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">Description</h3>
          <p className="text-sm">{node.description}</p>
        </section>

        <Separator />

        {/* 4. Assigned agent */}
        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Assigned agent</h3>
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <Badge variant="secondary">{node.agentKey}</Badge>
            <span className="text-muted-foreground">acting as {node.assignedRole}</span>
          </div>
        </section>

        <Separator />

        {/* 2. Required artifacts */}
        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Required artifacts</h3>
          {requiredArtifacts.length === 0 && freeformInputKeys.length === 0 ? (
            <p className="text-xs text-muted-foreground">None — this is the first stage.</p>
          ) : (
            <div className="space-y-1.5">
              {requiredArtifacts.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {requiredArtifacts.map((input) => (
                    <Badge key={input} variant="outline">
                      {formatSnakeCase(input)}
                    </Badge>
                  ))}
                </div>
              ) : null}
              {freeformInputKeys.length > 0 ? (
                <p className="text-xs text-muted-foreground">
                  Freeform input: {freeformInputKeys.map((k) => k.replace(/_/g, " ")).join(", ")}
                </p>
              ) : null}
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
              {isStoryCrafting && (
                <div className="mb-2">
                  <Select
                    value={storyCraftingMode}
                    onChange={(e) => setStoryCraftingMode(e.target.value as "VERTICAL" | "HORIZONTAL")}
                    className="text-xs"
                  >
                    <option value="VERTICAL">Vertical Stories</option>
                    <option value="HORIZONTAL">Horizontal Stories</option>
                  </Select>
                  <p className="mt-1 text-xs text-muted-foreground">{STORY_CRAFTING_MODE_HELPER_TEXT[storyCraftingMode]}</p>
                  <Input
                    value={sprintGoal}
                    onChange={(e) => setSprintGoal(e.target.value)}
                    placeholder="Sprint goal (optional)"
                    className="mt-2 h-8 text-xs"
                  />
                  <Input
                    value={teamCapacity}
                    onChange={(e) => setTeamCapacity(e.target.value)}
                    placeholder="Team capacity (optional)"
                    className="mt-2 h-8 text-xs"
                  />
                </div>
              )}
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

        {/* 5. Validator + 6. Quality score */}
        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Validator &amp; quality score</h3>
          {validator ? (
            <div className="space-y-1">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium">{validator.name}</span>
                <span className="text-xs text-muted-foreground">threshold {Math.round(validator.qualityThreshold * 100)}%</span>
              </div>
              {lastRun?.loopQualityScore != null ? (
                <div className="flex items-center gap-2">
                  <Badge variant={lastRun.loopQualityScore >= validator.qualityThreshold ? "success" : "warning"}>
                    {Math.round(lastRun.loopQualityScore * 100)}% quality
                  </Badge>
                  {lastRun.loopIteration > 0 ? (
                    <span className="text-xs text-muted-foreground">after {lastRun.loopIteration} iteration(s)</span>
                  ) : null}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">No quality score yet — no DRAFT run has completed validation.</p>
              )}
              {validator.criteria.length > 0 ? (
                <ul className="mt-1 list-inside list-disc text-xs text-muted-foreground">
                  {validator.criteria.slice(0, 3).map((c) => (
                    <li key={c} className="truncate">
                      {c}
                    </li>
                  ))}
                  {validator.criteria.length > 3 ? <li>+{validator.criteria.length - 3} more</li> : null}
                </ul>
              ) : null}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No validator configured for this stage.</p>
          )}
        </section>

        <Separator />

        {/* 7. Last agent run + 8. Token usage + 9. RAG sources used */}
        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Last agent run</h3>
          {lastRun ? (
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5">
                  <AgentRunStatusBadge status={lastRun.status} />
                  <Badge variant="outline">{formatSnakeCase(lastRun.action)}</Badge>
                </div>
                <span className="text-xs text-muted-foreground">{formatRelativeTime(lastRun.createdAt)}</span>
              </div>

              <div className="grid grid-cols-2 gap-x-2 gap-y-1 rounded-md border border-border p-2 text-xs">
                <span className="text-muted-foreground">Tokens</span>
                <span className="text-right font-medium">{lastRun.tokenUsage?.total_tokens ?? "—"}</span>
                <span className="text-muted-foreground">Context budget</span>
                <span className="text-right font-medium">
                  {lastRun.estimatedContextTokens ?? "—"} / {lastRun.contextTokenBudget ?? "—"}
                </span>
                <span className="text-muted-foreground">Output budget</span>
                <span className="text-right font-medium">{lastRun.outputTokenBudget ?? "—"}</span>
                <span className="text-muted-foreground">Cost</span>
                <span className="text-right font-medium">{formatCost(lastRun.cost)}</span>
              </div>

              <div>
                <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">RAG sources used</p>
                {lastRun.retrievedSourceTitles && lastRun.retrievedSourceTitles.length > 0 ? (
                  <div className="flex flex-wrap gap-1">
                    {lastRun.retrievedSourceTitles.map((title) => (
                      <Badge key={title} variant="secondary" className="max-w-full truncate">
                        {title}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    {lastRun.retrievedSourceTitles === null ? "Retrieval did not run for this run." : "No relevant sources found."}
                  </p>
                )}
              </div>

              {lastRun.errorMessage ? <p className="text-xs text-destructive">{lastRun.errorMessage}</p> : null}

              <Link href={`/agent-runs/${lastRun.id}`} className={buttonVariants({ variant: "outline", size: "sm", className: "w-full" })}>
                View full run
              </Link>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No agent run yet for this stage.</p>
          )}
        </section>

        <Separator />

        {/* 3. Approval requirements */}
        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Approval requirements</h3>
          {node.requiresHumanApproval ? (
            review ? (
              <div className="flex flex-col gap-1">
                <ReviewStatusBadge status={review.status} className="w-fit" />
                <p className="text-xs text-muted-foreground">
                  {review.reviewerName} · submitted {formatRelativeTime(review.submittedAt)}
                </p>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">Requires human approval — no review submitted yet.</p>
            )
          ) : (
            <p className="text-xs text-muted-foreground">This stage doesn&rsquo;t require human approval.</p>
          )}
        </section>

        <Separator />

        {/* 11. Available actions */}
        <section>
          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">Available actions</h3>
          <div className="flex flex-wrap gap-1">
            {node.allowedActions.map((allowedAction) => (
              <Badge key={allowedAction} variant="secondary">
                {formatSnakeCase(allowedAction)}
              </Badge>
            ))}
          </div>
        </section>

        {/* 12. Manual override — admins only */}
        {isAdmin ? (
          <>
            <Separator />
            <section>
              <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                <ShieldAlert className="h-3.5 w-3.5" />
                Manual override (admin)
              </h3>
              <div className="space-y-2 rounded-md border border-dashed border-border p-2.5">
                <Select
                  value={overrideStatus}
                  onChange={(e) => setOverrideStatus(e.target.value as WorkflowStatus)}
                  className="text-xs"
                >
                  {WORKFLOW_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {formatSnakeCase(s)}
                    </option>
                  ))}
                </Select>
                <Textarea
                  placeholder="Reason for override (required, audited)"
                  value={overrideReason}
                  onChange={(e) => setOverrideReason(e.target.value)}
                  rows={2}
                  className="text-xs"
                />
                <Button size="sm" variant="outline" className="w-full" onClick={handleOverride} disabled={overriding}>
                  {overriding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                  {overriding ? "Applying…" : "Apply override"}
                </Button>
                {overrideError ? <p className="text-xs text-destructive">{overrideError}</p> : null}
                {overrideDone ? <p className="text-xs text-emerald-600 dark:text-emerald-400">Override applied.</p> : null}
                <p className="text-[11px] text-muted-foreground">
                  Bypasses every graph rule. Every override is recorded with your user id and this reason.
                </p>
              </div>
            </section>
          </>
        ) : null}
      </div>
    </aside>
  );
}
