"""Loads workflow template JSON files and materializes them as a project's
WorkflowNode/WorkflowEdge rows.

The template (e.g. `workflows/sdlc-workflow.json`) is data, not a live
project — see docs/architecture.md. This module is the one place that
turns that static graph into a running project's actual rows.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Project, WorkflowEdge, WorkflowNode, WorkflowStatus


class WorkflowTemplateError(Exception):
    """Raised when a workflow template file is missing or malformed."""


@lru_cache
def load_workflow_template(file_name: str | None = None) -> dict[str, Any]:
    """Load and parse a workflow template JSON file.

    Cached because template files don't change at runtime; restart the
    process (or clear the cache) to pick up an edited template.
    """
    settings = get_settings()
    path = settings.WORKFLOWS_DIR / (file_name or settings.DEFAULT_WORKFLOW_FILE)
    if not path.is_file():
        raise WorkflowTemplateError(f"Workflow template not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        template = json.load(f)

    _validate_template(template, path)
    return template


def _validate_template(template: dict[str, Any], path: Path) -> None:
    required_top_level = ["id", "name", "version", "startNode", "nodes", "edges"]
    for field in required_top_level:
        if field not in template:
            raise WorkflowTemplateError(f"Workflow template {path} is missing required field '{field}'")

    node_ids = {node["id"] for node in template["nodes"]}
    if template["startNode"] not in node_ids:
        raise WorkflowTemplateError(f"Workflow template {path}: startNode '{template['startNode']}' is not a node id")

    required_node_fields = [
        "id",
        "name",
        "description",
        "agentKey",
        "requiredInputs",
        "outputArtifactType",
        "requiresHumanApproval",
        "allowedActions",
        "nextNodes",
    ]
    for node in template["nodes"]:
        for field in required_node_fields:
            if field not in node:
                raise WorkflowTemplateError(f"Workflow template {path}: node '{node.get('id')}' missing '{field}'")
        for next_id in node["nextNodes"]:
            if next_id not in node_ids:
                raise WorkflowTemplateError(
                    f"Workflow template {path}: node '{node['id']}' has unknown nextNodes entry '{next_id}'"
                )

    for edge in template["edges"]:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            raise WorkflowTemplateError(f"Workflow template {path}: edge {edge} references an unknown node")


def generate_workflow_graph(db: Session, project: Project, template: dict[str, Any]) -> None:
    """Create WorkflowNode/WorkflowEdge rows for `project` from `template`.

    The template's start node is created READY (its prerequisites are
    trivially satisfied — it has none); every other node starts LOCKED
    until GraphEngineService.unlock_next_nodes opens it up. Call this once,
    right after the project itself is created.
    """
    nodes_by_key: dict[str, WorkflowNode] = {}

    for order_index, node_data in enumerate(template["nodes"]):
        position = node_data.get("position", {"x": 0, "y": 0})
        node = WorkflowNode(
            project=project,
            node_key=node_data["id"],
            name=node_data["name"],
            description=node_data["description"],
            agent_key=node_data["agentKey"],
            required_inputs=node_data["requiredInputs"],
            output_artifact_type=node_data["outputArtifactType"],
            requires_human_approval=node_data["requiresHumanApproval"],
            allowed_actions=node_data["allowedActions"],
            status=WorkflowStatus.READY if node_data["id"] == template["startNode"] else WorkflowStatus.LOCKED,
            order_index=order_index,
            position_x=position.get("x", 0),
            position_y=position.get("y", 0),
        )
        db.add(node)
        nodes_by_key[node_data["id"]] = node

    # Flush so the nodes get IDs before edges reference them.
    db.flush()

    for edge_data in template["edges"]:
        db.add(
            WorkflowEdge(
                project=project,
                source_node=nodes_by_key[edge_data["source"]],
                target_node=nodes_by_key[edge_data["target"]],
                label=edge_data.get("label"),
            )
        )
