"use client";

import { useState } from "react";
import { CheckCircle2, FileText, Loader2, PlayCircle, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import {
  api,
  ApiError,
  ApiStory,
  ApiStoryArtifact,
  ApiStoryDeliveryLane,
  ApiStoryDeliveryNode,
  ApiUser,
} from "@/lib/api";

type Tab = "lane" | "lld";

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
  users,
  currentUserId,
}: {
  projectId: string;
  story: ApiStory;
  initialLane: ApiStoryDeliveryLane;
  initialNodes: ApiStoryDeliveryNode[];
  initialLld: ApiStoryArtifact | null;
  users: ApiUser[];
  currentUserId: string | null;
}) {
  const [tab, setTab] = useState<Tab>("lane");
  const [lane, setLane] = useState(initialLane);
  const [nodes, setNodes] = useState(initialNodes);
  const [lld, setLld] = useState(initialLld);
  const [busyNodeId, setBusyNodeId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const storyLldNode = nodes.find((n) => n.node_key === "STORY_LLD") ?? null;
  const lldReviewNode = nodes.find((n) => n.node_key === "LLD_REVIEW") ?? null;

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

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-1 border-b border-border">
        {(["lane", "lld"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              tab === t ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            {t === "lane" ? "Lane" : "LLD"}
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
                            {node.node_key === "LLD_REVIEW" ? "Approve (Tech Lead)" : "Complete"}
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
    </div>
  );
}
