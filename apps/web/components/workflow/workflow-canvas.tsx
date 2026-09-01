"use client";

import { useMemo, useState } from "react";
import ReactFlow, { Background, Controls, MiniMap, type Edge, type Node, type NodeMouseHandler } from "reactflow";
import "reactflow/dist/style.css";

import { NodeDetailsPanel } from "@/components/workflow/node-details-panel";
import { StageNode, type StageNodeData } from "@/components/workflow/stage-node";
import type {
  AgentRunDetail,
  DocumentArtifact,
  ProjectWorkflowEdge,
  ProjectWorkflowNode,
  ReviewItem,
  ValidatorDefinitionItem,
} from "@/lib/types";

const nodeTypes = { stage: StageNode };

export function WorkflowCanvas({
  projectId,
  nodes,
  edges,
  documents,
  reviews,
  agentRuns,
  validators,
  currentUserId,
  isAdmin,
}: {
  projectId: string;
  nodes: ProjectWorkflowNode[];
  edges: ProjectWorkflowEdge[];
  documents: DocumentArtifact[];
  reviews: ReviewItem[];
  /** All of this project's agent runs, newest-first or in any order — the
   * canvas picks each node's most recent one for the card/detail panel. */
  agentRuns: AgentRunDetail[];
  /** All validator definitions — matched to a node by stage === node.nodeKey. */
  validators: ValidatorDefinitionItem[];
  currentUserId: string | null;
  /** Gates the manual override section of the detail panel — see
   * apps/api/app/services/permissions.py's require_can_override_node. */
  isAdmin: boolean;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const selectedNode = nodes.find((n) => n.id === selectedNodeId) ?? null;

  // Same classification the document editor uses (see
  // apps/web/app/documents/[artifactId]/page.tsx) — a required input that
  // matches some node's output_artifact_type is an upstream-artifact
  // dependency, not something a human types in here.
  const knownArtifactTypes = useMemo(() => new Set(nodes.map((n) => n.outputArtifactType)), [nodes]);
  const selectedNodeFreeformInputKeys = useMemo(
    () => selectedNode?.requiredInputs.filter((input) => !knownArtifactTypes.has(input)) ?? [],
    [selectedNode, knownArtifactTypes]
  );

  // Each node's most recent agent run (by createdAt) — backs the quality
  // score / token cost shown on the card and the "Last agent run" /
  // "Token usage" / "RAG sources used" sections of the detail panel.
  const lastRunByNodeId = useMemo(() => {
    const map = new Map<string, AgentRunDetail>();
    for (const run of agentRuns) {
      const current = map.get(run.workflowNodeId);
      if (!current || run.createdAt > current.createdAt) map.set(run.workflowNodeId, run);
    }
    return map;
  }, [agentRuns]);

  const validatorByStage = useMemo(() => {
    const map = new Map<string, ValidatorDefinitionItem>();
    for (const v of validators) map.set(v.stage, v);
    return map;
  }, [validators]);

  const flowNodes = useMemo<Node<StageNodeData>[]>(
    () =>
      nodes.map((node) => {
        const lastRun = lastRunByNodeId.get(node.id) ?? null;
        return {
          id: node.id,
          type: "stage",
          position: node.position,
          data: {
            label: node.name,
            status: node.status,
            assignedRole: node.assignedRole,
            outputArtifactType: node.outputArtifactType,
            requiresHumanApproval: node.requiresHumanApproval,
            qualityScore: lastRun?.loopQualityScore ?? null,
            lastRunCost: lastRun?.cost ?? null,
            blockedReason: node.blockedReason,
            selected: node.id === selectedNodeId,
          },
        };
      }),
    [nodes, selectedNodeId, lastRunByNodeId]
  );

  const flowEdges = useMemo<Edge[]>(
    () =>
      edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edge.label,
        animated: edge.label === "rework",
        style: edge.label === "rework" ? { stroke: "hsl(var(--destructive))" } : undefined,
      })),
    [edges]
  );

  const handleNodeClick: NodeMouseHandler = (_event, node) => {
    setSelectedNodeId((current) => (current === node.id ? null : node.id));
  };

  return (
    // Column below lg: side-by-side with a fixed-width detail panel leaves
    // almost no room for the canvas on a phone/tablet-portrait screen —
    // stack them instead so both stay fully usable.
    <div className="flex flex-col gap-4 lg:h-[70vh] lg:flex-row">
      <div className="h-[50vh] min-w-0 overflow-hidden rounded-lg border border-border lg:h-auto lg:flex-1">
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={nodeTypes}
          onNodeClick={handleNodeClick}
          onPaneClick={() => setSelectedNodeId(null)}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={16} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable className="!bg-card" />
        </ReactFlow>
      </div>

      {selectedNode ? (
        <NodeDetailsPanel
          projectId={projectId}
          node={selectedNode}
          documents={documents}
          reviews={reviews}
          freeformInputKeys={selectedNodeFreeformInputKeys}
          lastRun={lastRunByNodeId.get(selectedNode.id) ?? null}
          validator={validatorByStage.get(selectedNode.nodeKey) ?? null}
          currentUserId={currentUserId}
          isAdmin={isAdmin}
          onClose={() => setSelectedNodeId(null)}
        />
      ) : null}
    </div>
  );
}
