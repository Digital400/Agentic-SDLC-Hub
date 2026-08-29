"use client";

import { useMemo, useState } from "react";
import ReactFlow, { Background, Controls, MiniMap, type Edge, type Node, type NodeMouseHandler } from "reactflow";
import "reactflow/dist/style.css";

import { NodeDetailsPanel } from "@/components/workflow/node-details-panel";
import { StageNode, type StageNodeData } from "@/components/workflow/stage-node";
import type { DocumentArtifact, ProjectWorkflowEdge, ProjectWorkflowNode, ReviewItem } from "@/lib/types";

const nodeTypes = { stage: StageNode };

export function WorkflowCanvas({
  projectId,
  nodes,
  edges,
  documents,
  reviews,
}: {
  projectId: string;
  nodes: ProjectWorkflowNode[];
  edges: ProjectWorkflowEdge[];
  documents: DocumentArtifact[];
  reviews: ReviewItem[];
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const selectedNode = nodes.find((n) => n.id === selectedNodeId) ?? null;

  const flowNodes = useMemo<Node<StageNodeData>[]>(
    () =>
      nodes.map((node) => ({
        id: node.id,
        type: "stage",
        position: node.position,
        data: {
          label: node.name,
          status: node.status,
          assignedRole: node.assignedRole,
          outputArtifactType: node.outputArtifactType,
          requiresHumanApproval: node.requiresHumanApproval,
          selected: node.id === selectedNodeId,
        },
      })),
    [nodes, selectedNodeId]
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
    <div className="flex h-[70vh] w-full gap-4">
      <div className="min-w-0 flex-1 overflow-hidden rounded-lg border border-border">
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
          onClose={() => setSelectedNodeId(null)}
        />
      ) : null}
    </div>
  );
}
