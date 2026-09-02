"use client";

import { useState } from "react";
import { CheckCircle2, FileText, GitPullRequest, Loader2, PlayCircle, RefreshCw, ShieldAlert, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  api,
  ApiError,
  ApiImplementationRun,
  ApiImplementationTask,
  ApiPRReviewRun,
  ApiStory,
  ApiStoryArtifact,
  ApiStoryDeliveryLane,
  ApiStoryDeliveryNode,
  ApiTestAgentType,
  ApiTestRun,
  ApiUser,
} from "@/lib/api";

type Tab = "lane" | "lld" | "implementation-plan" | "implementation" | "pr-review" | "testing";

const TAB_LABELS: Record<Tab, string> = {
  lane: "Lane",
  lld: "LLD",
  "implementation-plan": "Implementation Plan",
  implementation: "Implementation",
  "pr-review": "PR Review",
  testing: "Testing",
};

const TEST_AGENT_TYPES: ApiTestAgentType[] = ["UNIT", "API", "UI", "REGRESSION", "SECURITY"];

function nodeStatusVariant(status: ApiStoryDeliveryNode["status"]): "gray" | "info" | "warning" | "destructive" | "success" {
  switch (status) {
    case "LOCKED":
      return "gray";
    case "READY":
      return "info";
    case "IN_PROGRESS":
      return "warning";
    case "WAITING_FOR_REVIEW":
      return "warning";
    case "BLOCKED":
      return "destructive";
    case "COMPLETED":
      return "success";
  }
}

export function StoryLaneWorkspace({
  projectId,
  story,
  initialLane,
  initialNodes,
  initialLld,
  initialImplementationPlan,
  initialImplementationTask,
  initialImplementationRuns,
  initialTestRuns,
  initialPrReviewRuns,
  initialTestReport,
  users,
  currentUserId,
}: {
  projectId: string;
  story: ApiStory;
  initialLane: ApiStoryDeliveryLane;
  initialNodes: ApiStoryDeliveryNode[];
  initialLld: ApiStoryArtifact | null;
  initialImplementationPlan: ApiStoryArtifact | null;
  initialImplementationTask: ApiImplementationTask | null;
  initialImplementationRuns: ApiImplementationRun[];
  initialTestRuns: ApiTestRun[];
  initialPrReviewRuns: ApiPRReviewRun[];
  initialTestReport: ApiStoryArtifact | null;
  users: ApiUser[];
  currentUserId: string | null;
}) {
  const [tab, setTab] = useState<Tab>("lane");
  const [lane, setLane] = useState(initialLane);
  const [nodes, setNodes] = useState(initialNodes);
  const [lld, setLld] = useState(initialLld);
  const [implementationPlan, setImplementationPlan] = useState(initialImplementationPlan);
  const [implementationTask, setImplementationTask] = useState(initialImplementationTask);
  const [implementationRuns, setImplementationRuns] = useState(initialImplementationRuns);
  const [testRuns, setTestRuns] = useState(initialTestRuns);
  const [prReviewRuns, setPrReviewRuns] = useState(initialPrReviewRuns);
  const [testReport, setTestReport] = useState(initialTestReport);
  const [testAgentType, setTestAgentType] = useState<ApiTestAgentType>("UNIT");
  const [busyNodeId, setBusyNodeId] = useState<string | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const [testRunBusy, setTestRunBusy] = useState(false);
  const [prReviewBusy, setPrReviewBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const storyLldNode = nodes.find((n) => n.node_key === "STORY_LLD") ?? null;
  const lldReviewNode = nodes.find((n) => n.node_key === "LLD_REVIEW") ?? null;
  const implementationPlanNode = nodes.find((n) => n.node_key === "IMPLEMENTATION_PLAN") ?? null;
  const implementationLaneNode = nodes.find((n) => n.node_key === "IMPLEMENTATION") ?? null;
  const prReviewLaneNode = nodes.find((n) => n.node_key === "PR_REVIEW_AGENT") ?? null;
  const humanCodeReviewNode = nodes.find((n) => n.node_key === "HUMAN_CODE_REVIEW") ?? null;
  const testingLaneNode = nodes.find((n) => n.node_key === "TESTING") ?? null;
  const qaApprovalNode = nodes.find((n) => n.node_key === "QA_APPROVAL") ?? null;
  const latestRun = implementationRuns[0] ?? null;
  const latestTestRun = testRuns[0] ?? null;
  const latestPrReviewRun = prReviewRuns[0] ?? null;

  async function refresh() {
    const [refreshedLane, refreshedNodes] = await Promise.all([
      api.stories.getLane(story.id),
      api.storyDelivery.listNodes(lane.id),
    ]);
    setLane(refreshedLane);
    setNodes(refreshedNodes);
    try {
      setLld(await api.stories.getLld(story.id));
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) throw err;
      setLld(null);
    }
    try {
      setImplementationPlan(await api.stories.getImplementationPlan(story.id));
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) throw err;
      setImplementationPlan(null);
    }
    try {
      const task = await api.stories.getImplementationTask(story.id);
      setImplementationTask(task);
      setImplementationRuns(await api.projects.implementationTaskRuns(projectId, task.id));
      setTestRuns(await api.projects.implementationTaskTestRuns(projectId, task.id));
      setPrReviewRuns(await api.projects.implementationTaskPrReviewRuns(projectId, task.id));
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) throw err;
      setImplementationTask(null);
      setImplementationRuns([]);
      setTestRuns([]);
      setPrReviewRuns([]);
    }
    try {
      setTestReport(await api.stories.getTestReport(story.id));
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) throw err;
      setTestReport(null);
    }
  }

  async function handleStartImplementation() {
    if (currentUserId === null || implementationTask === null) return;
    setRunBusy(true);
    setError(null);
    try {
      await api.implementationRuns.start({ implementation_task_id: implementationTask.id, triggered_by_user_id: currentUserId });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start implementation.");
    } finally {
      setRunBusy(false);
    }
  }

  async function handleReviewRun(decision: "ACCEPTED" | "REJECTED") {
    if (currentUserId === null || latestRun === null) return;
    setRunBusy(true);
    setError(null);
    try {
      await api.implementationRuns.review(latestRun.id, { decision, reviewed_by_user_id: currentUserId });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to record this review decision.");
    } finally {
      setRunBusy(false);
    }
  }

  async function handleCreatePullRequest() {
    if (currentUserId === null || latestRun === null) return;
    setRunBusy(true);
    setError(null);
    try {
      await api.implementationRuns.createPullRequest(latestRun.id, { triggered_by_user_id: currentUserId });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create the pull request.");
    } finally {
      setRunBusy(false);
    }
  }

  async function handleStartPrReview() {
    if (currentUserId === null || implementationTask === null) return;
    setPrReviewBusy(true);
    setError(null);
    try {
      await api.prReviewRuns.start({ implementation_task_id: implementationTask.id, triggered_by_user_id: currentUserId });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start the PR review.");
    } finally {
      setPrReviewBusy(false);
    }
  }

  async function handleStartTesting() {
    if (currentUserId === null || implementationTask === null) return;
    setTestRunBusy(true);
    setError(null);
    try {
      await api.testRuns.start({
        implementation_task_id: implementationTask.id, agent_type: testAgentType, triggered_by_user_id: currentUserId,
      });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start testing.");
    } finally {
      setTestRunBusy(false);
    }
  }

  async function handleQaApproval(decision: "COMPLETED" | "BLOCKED") {
    if (currentUserId === null || qaApprovalNode === null) return;
    setTestRunBusy(true);
    setError(null);
    try {
      await api.storyDelivery.updateNodeStatus(qaApprovalNode.id, {
        status: decision, actor_user_id: currentUserId,
        blocked_reason: decision === "BLOCKED" ? "QA rejected this story's test evidence." : undefined,
      });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to record the QA decision.");
    } finally {
      setTestRunBusy(false);
    }
  }

  async function handleAdvance(node: ApiStoryDeliveryNode, newStatus: string) {
    if (currentUserId === null) return;
    setBusyNodeId(node.id);
    setError(null);
    try {
      await api.storyDelivery.updateNodeStatus(node.id, { status: newStatus, actor_user_id: currentUserId });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update this node.");
    } finally {
      setBusyNodeId(null);
    }
  }

  async function handleDraftLld() {
    if (currentUserId === null || storyLldNode === null) return;
    setBusyNodeId(storyLldNode.id);
    setError(null);
    try {
      const result = await api.storyDelivery.draftStoryLld(storyLldNode.id, currentUserId);
      if (result.needs_clarification) {
        setError("The agent needs more information before it can draft this story's LLD — see the generated notes.");
      }
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to draft the Story LLD.");
    } finally {
      setBusyNodeId(null);
    }
  }

  async function handleDraftImplementationPlan() {
    if (currentUserId === null || implementationPlanNode === null) return;
    setBusyNodeId(implementationPlanNode.id);
    setError(null);
    try {
      const result = await api.storyDelivery.draftImplementationPlan(implementationPlanNode.id, currentUserId);
      if (result.needs_clarification) {
        setError("The agent needs more information before it can draft this story's Implementation Plan — see the generated notes.");
      }
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to draft the Implementation Plan.");
    } finally {
      setBusyNodeId(null);
    }
  }

  async function handleAcceptImplementationPlan() {
    if (currentUserId === null || implementationPlanNode === null) return;
    setBusyNodeId(implementationPlanNode.id);
    setError(null);
    try {
      await api.storyDelivery.updateNodeStatus(implementationPlanNode.id, { status: "COMPLETED", actor_user_id: currentUserId });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to accept this Implementation Plan — only its assigned user or a Tech Lead may.");
    } finally {
      setBusyNodeId(null);
    }
  }

  async function handleRequestChangesOnImplementationPlan() {
    if (currentUserId === null || implementationPlanNode === null) return;
    const reason = window.prompt("What changes are needed?") ?? "";
    setBusyNodeId(implementationPlanNode.id);
    setError(null);
    try {
      await api.storyDelivery.updateNodeStatus(implementationPlanNode.id, {
        status: "BLOCKED", actor_user_id: currentUserId, blocked_reason: reason || "Changes requested.",
      });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to request changes on this Implementation Plan.");
    } finally {
      setBusyNodeId(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-1 border-b border-border">
        {(["lane", "lld", "implementation-plan", "implementation", "pr-review", "testing"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              tab === t ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {tab === "lane" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Delivery lane</CardTitle>
            <CardDescription>Lane status: {lane.status}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {nodes
              .slice()
              .sort((a, b) => a.order_index - b.order_index)
              .map((node) => {
                const busy = busyNodeId === node.id;
                const isCurrent = lane.current_node_id === node.id;
                return (
                  <div
                    key={node.id}
                    className={cn(
                      "flex items-center justify-between gap-3 rounded-md border p-2.5",
                      isCurrent ? "border-primary" : "border-border"
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{node.name}</span>
                      <Badge variant={nodeStatusVariant(node.status)}>{node.status}</Badge>
                      {node.requires_approval && <Badge variant="purple">Review gate</Badge>}
                      {node.assigned_role && <span className="text-xs text-muted-foreground">({node.assigned_role})</span>}
                    </div>
                    <div className="flex gap-2">
                      {node.status === "READY" && node.node_key !== "STORY_LLD" && (
                        <Button
                          size="sm" variant="outline"
                          onClick={() => handleAdvance(node, "IN_PROGRESS")}
                          disabled={busy || currentUserId === null}
                        >
                          {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <PlayCircle className="mr-1 h-3 w-3" />}
                          Start
                        </Button>
                      )}
                      {(node.status === "READY" || node.status === "IN_PROGRESS" || node.status === "WAITING_FOR_REVIEW") &&
                        node.node_key !== "STORY_LLD" && (
                          <Button
                            size="sm"
                            onClick={() => handleAdvance(node, "COMPLETED")}
                            disabled={busy || currentUserId === null}
                          >
                            {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <CheckCircle2 className="mr-1 h-3 w-3" />}
                            {node.node_key === "LLD_REVIEW"
                              ? "Approve (Tech Lead)"
                              : node.node_key === "RELEASE_READY"
                                ? "Mark Done (Product Owner)"
                                : "Complete"}
                          </Button>
                        )}
                    </div>
                  </div>
                );
              })}
          </CardContent>
        </Card>
      )}

      {tab === "lld" && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">Story LLD</CardTitle>
              <CardDescription>
                Scoped to exactly this story. Requires the HLD to be approved.
                {lldReviewNode && <> Review gate: {lldReviewNode.status} (Tech Lead approval).</>}
              </CardDescription>
            </div>
            {storyLldNode && storyLldNode.status !== "LOCKED" && (
              <Button size="sm" onClick={handleDraftLld} disabled={busyNodeId === storyLldNode.id || currentUserId === null}>
                {busyNodeId === storyLldNode.id ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="mr-1 h-3.5 w-3.5" />
                )}
                {lld ? "Regenerate" : "Draft Story LLD"}
              </Button>
            )}
          </CardHeader>
          <CardContent>
            {storyLldNode?.status === "LOCKED" ? (
              <p className="text-sm text-muted-foreground">
                Story LLD is locked — Story Ready must complete first.
              </p>
            ) : lld ? (
              <div className="max-h-[70vh] overflow-y-auto rounded-md border border-border bg-muted/30 p-4">
                <pre className="whitespace-pre-wrap font-sans text-sm">{lld.content_markdown}</pre>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-muted-foreground">
                <FileText className="h-6 w-6" />
                No Story LLD has been drafted yet.
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {tab === "implementation-plan" && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">Implementation Plan</CardTitle>
              <CardDescription>
                Scoped to exactly this story. Requires Story LLD approval. Does not generate code — see the
                Implementation tab for that.
                {implementationPlanNode && <> Node: {implementationPlanNode.status}.</>}
              </CardDescription>
            </div>
            {implementationPlanNode && implementationPlanNode.status !== "LOCKED" && (
              <Button
                size="sm" variant="outline" onClick={handleDraftImplementationPlan}
                disabled={busyNodeId === implementationPlanNode.id || currentUserId === null}
              >
                {busyNodeId === implementationPlanNode.id ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="mr-1 h-3.5 w-3.5" />
                )}
                {implementationPlan ? "Regenerate" : "Draft Implementation Plan"}
              </Button>
            )}
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {implementationPlanNode?.status === "LOCKED" ? (
              <p className="text-sm text-muted-foreground">Implementation Plan is locked — Story LLD must be approved first.</p>
            ) : implementationPlan ? (
              <>
                <div className="max-h-[70vh] overflow-y-auto rounded-md border border-border bg-muted/30 p-4">
                  <pre className="whitespace-pre-wrap font-sans text-sm">{implementationPlan.content_markdown}</pre>
                </div>
                {implementationPlanNode && implementationPlanNode.status !== "COMPLETED" && (
                  <div className="flex gap-2">
                    <Button size="sm" onClick={handleAcceptImplementationPlan} disabled={busyNodeId === implementationPlanNode.id}>
                      <CheckCircle2 className="mr-1 h-3.5 w-3.5" /> Accept
                    </Button>
                    <Button
                      size="sm" variant="outline" onClick={handleRequestChangesOnImplementationPlan}
                      disabled={busyNodeId === implementationPlanNode.id}
                    >
                      <XCircle className="mr-1 h-3.5 w-3.5" /> Request Changes
                    </Button>
                  </div>
                )}
                {implementationPlanNode?.status === "COMPLETED" && <Badge variant="success">Accepted</Badge>}
                {implementationPlanNode?.status === "BLOCKED" && implementationPlanNode.blocked_reason && (
                  <p className="text-sm text-destructive">Changes requested: {implementationPlanNode.blocked_reason}</p>
                )}
              </>
            ) : (
              <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-muted-foreground">
                <FileText className="h-6 w-6" />
                No Implementation Plan has been drafted yet.
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {tab === "implementation" && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">Implementation</CardTitle>
              <CardDescription>
                One task, one story. Requires Story LLD (LLD_REVIEW) approval — created automatically once it happens.
                {implementationLaneNode && <> Node: {implementationLaneNode.status}.</>}
              </CardDescription>
            </div>
            {implementationTask && (
              <Button size="sm" onClick={handleStartImplementation} disabled={runBusy || currentUserId === null}>
                {runBusy ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1 h-3.5 w-3.5" />}
                {latestRun ? "Regenerate" : "Start Implementation"}
              </Button>
            )}
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {implementationTask === null ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-muted-foreground">
                <FileText className="h-6 w-6" />
                Not available yet — approve this story's LLD (LLD_REVIEW) first.
              </div>
            ) : (
              <>
                <div className="text-xs text-muted-foreground">
                  {implementationTask.title} · {implementationTask.area} · {implementationTask.assigned_agent_type}
                </div>

                {latestRun && (
                  <div className="flex flex-col gap-3">
                    <div className="flex items-center gap-2">
                      <Badge variant={latestRun.status === "COMPLETED" ? "success" : latestRun.status === "FAILED" ? "destructive" : "info"}>
                        {latestRun.status}
                      </Badge>
                      <Badge variant={latestRun.review_status === "ACCEPTED" ? "success" : latestRun.review_status === "REJECTED" ? "destructive" : "gray"}>
                        {latestRun.review_status}
                      </Badge>
                      {latestRun.used_mock && <span className="text-xs text-muted-foreground">(mock provider)</span>}
                    </div>

                    {latestRun.error_message ? (
                      <p className="text-sm text-destructive">{latestRun.error_message}</p>
                    ) : (
                      <>
                        <div>
                          <p className="mb-1 text-xs font-medium text-muted-foreground">Summary</p>
                          <p className="text-sm">{latestRun.explanation}</p>
                        </div>

                        <div>
                          <p className="mb-1 text-xs font-medium text-muted-foreground">Changed files</p>
                          <ul className="text-xs">
                            {latestRun.proposed_file_changes.map((c) => (
                              <li key={c.path}>
                                <span className="font-mono">{c.path}</span> — {c.change_type}
                              </li>
                            ))}
                          </ul>
                        </div>

                        <div>
                          <p className="mb-1 text-xs font-medium text-muted-foreground">Diff / patch</p>
                          <div className="max-h-64 overflow-y-auto rounded-md border border-border bg-muted/30 p-3">
                            <pre className="whitespace-pre-wrap font-mono text-xs">{latestRun.diff_text || "(no diff)"}</pre>
                          </div>
                        </div>

                        <p className="text-xs">
                          <span className="font-medium text-muted-foreground">Test command: </span>
                          <span className="font-mono">{latestRun.test_command || "N/A"}</span>
                        </p>

                        {latestRun.risks.length > 0 && (
                          <div>
                            <p className="mb-1 text-xs font-medium text-muted-foreground">Risks / blockers</p>
                            <ul className="list-inside list-disc text-xs text-muted-foreground">
                              {latestRun.risks.map((r, i) => (
                                <li key={i}>{r}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        <div>
                          <p className="mb-1 text-xs font-medium text-muted-foreground">PR description</p>
                          <div className="rounded-md border border-border bg-muted/30 p-3">
                            <pre className="whitespace-pre-wrap font-sans text-xs">{latestRun.pr_description || "(none)"}</pre>
                          </div>
                        </div>

                        {latestRun.status === "COMPLETED" && latestRun.review_status === "PENDING_REVIEW" && (
                          <div className="flex gap-2">
                            <Button size="sm" onClick={() => handleReviewRun("ACCEPTED")} disabled={runBusy}>
                              <CheckCircle2 className="mr-1 h-3.5 w-3.5" /> Accept
                            </Button>
                            <Button size="sm" variant="outline" onClick={() => handleReviewRun("REJECTED")} disabled={runBusy}>
                              <XCircle className="mr-1 h-3.5 w-3.5" /> Reject
                            </Button>
                          </div>
                        )}

                        {latestRun.review_status === "ACCEPTED" && !latestRun.pull_request && (
                          <Button size="sm" onClick={handleCreatePullRequest} disabled={runBusy} className="w-fit">
                            <GitPullRequest className="mr-1 h-3.5 w-3.5" /> Create Pull Request
                          </Button>
                        )}
                        {latestRun.pull_request && (
                          <a
                            href={latestRun.pull_request.pr_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex w-fit items-center gap-1 text-sm font-medium text-primary underline-offset-2 hover:underline"
                          >
                            <GitPullRequest className="h-3.5 w-3.5" /> PR #{latestRun.pull_request.pr_number} (branch {latestRun.pull_request.branch_name})
                          </a>
                        )}
                      </>
                    )}
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      )}

      {tab === "pr-review" && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">PR Review</CardTitle>
              <CardDescription>
                Requires an accepted implementation run with a created pull request.
                {prReviewLaneNode && <> Node: {prReviewLaneNode.status}.</>}
                {humanCodeReviewNode && <> Human Code Review: {humanCodeReviewNode.status} — the real GitHub PR review, external to this app.</>}
              </CardDescription>
            </div>
            {implementationTask && latestRun?.pull_request && (
              <Button size="sm" onClick={handleStartPrReview} disabled={prReviewBusy || currentUserId === null}>
                {prReviewBusy ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1 h-3.5 w-3.5" />}
                {latestPrReviewRun ? "Re-run" : "Start PR Review"}
              </Button>
            )}
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {implementationTask === null ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-muted-foreground">
                <FileText className="h-6 w-6" />
                Not available yet — approve this story's LLD first.
              </div>
            ) : !latestRun?.pull_request ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-muted-foreground">
                <GitPullRequest className="h-6 w-6" />
                Create a pull request from the Implementation tab first.
              </div>
            ) : latestPrReviewRun === null ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-muted-foreground">
                <FileText className="h-6 w-6" />
                No PR review run yet.
              </div>
            ) : (
              <>
                <div className="flex items-center gap-2">
                  <Badge variant={latestPrReviewRun.status === "COMPLETED" ? "success" : latestPrReviewRun.status === "FAILED" ? "destructive" : "info"}>
                    {latestPrReviewRun.status}
                  </Badge>
                  {latestPrReviewRun.overall_recommendation && (
                    <Badge variant={latestPrReviewRun.overall_recommendation === "APPROVE" ? "success" : "warning"}>
                      {latestPrReviewRun.overall_recommendation}
                    </Badge>
                  )}
                  {latestPrReviewRun.risk_score !== null && (
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <ShieldAlert className="h-3 w-3" /> Risk: {latestPrReviewRun.risk_score}
                    </span>
                  )}
                  {latestPrReviewRun.used_mock && <span className="text-xs text-muted-foreground">(mock provider)</span>}
                </div>

                {latestPrReviewRun.error_message ? (
                  <p className="text-sm text-destructive">{latestPrReviewRun.error_message}</p>
                ) : (
                  <>
                    <div>
                      <p className="mb-1 text-xs font-medium text-muted-foreground">Summary</p>
                      <p className="text-sm">{latestPrReviewRun.summary}</p>
                    </div>

                    {[
                      { label: "Critical findings", items: latestPrReviewRun.critical_findings },
                      { label: "Major findings", items: latestPrReviewRun.major_findings },
                      { label: "Minor findings", items: latestPrReviewRun.minor_findings },
                    ].map(
                      ({ label, items }) =>
                        items.length > 0 && (
                          <div key={label}>
                            <p className="mb-1 text-xs font-medium text-muted-foreground">{label}</p>
                            <ul className="list-inside list-disc text-xs">
                              {items.map((f, i) => (
                                <li key={i}>
                                  <span className="font-mono">{f.file}</span> — {f.detail}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )
                    )}

                    {latestPrReviewRun.missing_tests.length > 0 && (
                      <div>
                        <p className="mb-1 text-xs font-medium text-muted-foreground">Missing tests</p>
                        <ul className="list-inside list-disc text-xs text-muted-foreground">
                          {latestPrReviewRun.missing_tests.map((t, i) => (
                            <li key={i}>{t}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    <p className="text-xs">
                      <span className="font-medium text-muted-foreground">Final reviewer note: </span>
                      {latestPrReviewRun.final_reviewer_note || "(none)"}
                    </p>
                  </>
                )}
              </>
            )}
          </CardContent>
        </Card>
      )}

      {tab === "testing" && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">Testing</CardTitle>
              <CardDescription>
                Requires an accepted implementation run (or PR).
                {testingLaneNode && <> Node: {testingLaneNode.status}.</>}
                {qaApprovalNode && <> QA Approval: {qaApprovalNode.status} (requires test evidence).</>}
              </CardDescription>
            </div>
            {implementationTask && (
              <div className="flex items-center gap-2">
                <Select value={testAgentType} onChange={(e) => setTestAgentType(e.target.value as ApiTestAgentType)} className="w-32 text-xs">
                  {TEST_AGENT_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </Select>
                <Button size="sm" onClick={handleStartTesting} disabled={testRunBusy || currentUserId === null}>
                  {testRunBusy ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="mr-1 h-3.5 w-3.5" />}
                  {latestTestRun ? "Re-run" : "Start Testing"}
                </Button>
              </div>
            )}
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {implementationTask === null ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-muted-foreground">
                <FileText className="h-6 w-6" />
                Not available yet — approve this story's LLD first.
              </div>
            ) : latestTestRun === null ? (
              <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-muted-foreground">
                <FileText className="h-6 w-6" />
                No test run yet.
              </div>
            ) : (
              <>
                <div className="flex items-center gap-2">
                  <Badge variant={latestTestRun.status === "COMPLETED" ? "success" : latestTestRun.status === "FAILED" ? "destructive" : "info"}>
                    {latestTestRun.status}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    Pass: {latestTestRun.pass_count} · Fail: {latestTestRun.fail_count}
                  </span>
                  {latestTestRun.used_mock && <span className="text-xs text-muted-foreground">(mock provider)</span>}
                </div>

                {latestTestRun.error_message ? (
                  <p className="text-sm text-destructive">{latestTestRun.error_message}</p>
                ) : (
                  <>
                    <div>
                      <p className="mb-1 text-xs font-medium text-muted-foreground">Test plan</p>
                      <p className="whitespace-pre-wrap text-sm">{latestTestRun.test_plan || "(none)"}</p>
                    </div>

                    {latestTestRun.tests_to_add.length > 0 && (
                      <div>
                        <p className="mb-1 text-xs font-medium text-muted-foreground">Tests added</p>
                        <ul className="list-inside list-disc text-xs">
                          {latestTestRun.tests_to_add.map((t) => (
                            <li key={t.name}>
                              <span className="font-mono">{t.name}</span> ({t.area}) — {t.description}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {latestTestRun.tests_executed.length > 0 && (
                      <div>
                        <p className="mb-1 text-xs font-medium text-muted-foreground">Tests executed</p>
                        <ul className="text-xs">
                          {latestTestRun.tests_executed.map((t) => (
                            <li key={t.name}>
                              [{t.result}] <span className="font-mono">{t.name}</span> — {t.notes}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {latestTestRun.bugs_found.length > 0 && (
                      <div>
                        <p className="mb-1 text-xs font-medium text-muted-foreground">Bugs found</p>
                        <ul className="list-inside list-disc text-xs text-muted-foreground">
                          {latestTestRun.bugs_found.map((b, i) => (
                            <li key={i}>{b}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    <div>
                      <p className="mb-1 text-xs font-medium text-muted-foreground">Coverage impact</p>
                      <p className="text-xs text-muted-foreground">{latestTestRun.coverage_impact.note ?? "(none)"}</p>
                    </div>

                    {testReport && (
                      <div>
                        <p className="mb-1 text-xs font-medium text-muted-foreground">Full test report</p>
                        <div className="max-h-64 overflow-y-auto rounded-md border border-border bg-muted/30 p-3">
                          <pre className="whitespace-pre-wrap font-sans text-xs">{testReport.content_markdown}</pre>
                        </div>
                      </div>
                    )}

                    {qaApprovalNode && qaApprovalNode.status === "READY" && (
                      <div className="flex gap-2">
                        <Button size="sm" onClick={() => handleQaApproval("COMPLETED")} disabled={testRunBusy}>
                          <CheckCircle2 className="mr-1 h-3.5 w-3.5" /> Grant QA Approval
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => handleQaApproval("BLOCKED")} disabled={testRunBusy}>
                          <XCircle className="mr-1 h-3.5 w-3.5" /> Reject
                        </Button>
                      </div>
                    )}
                  </>
                )}
              </>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
