"""Input hashing and downstream invalidation.

Re-planning is only meaningful if changing an upstream output actually
invalidates the work that depended on it. Each node records a hash of the inputs
it consumed; when that hash changes the node and everything downstream of it
returns to pending, while the audit log keeps the original history.
"""

import hashlib
import json
from pathlib import Path

from app.orchestrator.models import NodeState, NodeStatus, RunState
from app.orchestrator.planner import JOIN_STAGES

RESETTABLE = {
    NodeStatus.SUCCEEDED,
    NodeStatus.FAILED,
    NodeStatus.ROLLED_BACK,
    NodeStatus.STOPPED,
}

REFRESH_STAGES = {
    "understand",
    "confirm_scope",
    "decompose",
    "impact_analysis",
    "design",
    "apply_assumptions",
}


def compute_input_hash(run: RunState, node: NodeState, artifacts: Path) -> str:
    """Hash the spec, assumptions, and upstream artifacts. Understand also hashes the requirement."""

    digest = hashlib.sha256()
    if node.spec.stage == "understand":
        digest.update(run.requirement.encode("utf-8"))
    digest.update(json.dumps(run.assumptions, sort_keys=True).encode("utf-8"))
    digest.update(node.spec.model_dump_json().encode("utf-8"))
    for dep_id in sorted(node.spec.requires):
        upstream = run.nodes.get(dep_id)
        if upstream is None:
            continue
        for name in sorted(upstream.spec.produces):
            path = artifacts / name
            digest.update(name.encode("utf-8"))
            if path.exists():
                digest.update(path.read_bytes())
    return digest.hexdigest()[:32]


def descendants(run: RunState, node_ids: set[str]) -> set[str]:
    """Every node reachable downstream of the given nodes."""

    found: set[str] = set()
    frontier = set(node_ids)
    while frontier:
        current = frontier.pop()
        for candidate_id, candidate in run.nodes.items():
            if current in candidate.spec.requires and candidate_id not in found:
                found.add(candidate_id)
                frontier.add(candidate_id)
    return found


def _reset_node(node: NodeState) -> None:
    node.status = NodeStatus.PENDING
    node.finished_at = None
    node.error = None
    node.fallback_applied = False
    node.input_hash = None


def invalidate_stale(run: RunState, artifacts: Path) -> list[str]:
    """Reset nodes whose inputs changed, plus everything downstream of them."""

    changed: set[str] = set()
    for node_id, node in run.nodes.items():
        if node.input_hash is None:
            continue
        current = compute_input_hash(run, node, artifacts)
        if current != node.input_hash:
            changed.add(node_id)

    affected = changed | descendants(run, changed)
    reset: list[str] = []
    for node_id in sorted(affected):
        node = run.nodes[node_id]
        if node.status not in RESETTABLE:
            continue
        _reset_node(node)
        reset.append(node_id)
    return reset


def apply_amend_invalidation(
    run: RunState,
    added: list[str],
    removed_produces: dict[str, list[str]],
    runs_dir: str,
) -> list[str]:
    """Surgical amend: keep sibling implement nodes, reset join stages and new work."""

    from app.orchestrator.store import clear_snapshot
    from app.orchestrator.workspace import delete_artifact_files

    clear_snapshot(runs_dir, run.id)
    reset: list[str] = []
    for names in removed_produces.values():
        delete_artifact_files(runs_dir, run.id, names)

    for node_id in added:
        node = run.nodes.get(node_id)
        if node is None:
            continue
        _reset_node(node)
        reset.append(node_id)

    for node_id, node in run.nodes.items():
        if node.spec.stage in JOIN_STAGES | REFRESH_STAGES and node.status in RESETTABLE:
            _reset_node(node)
            if node_id not in reset:
                reset.append(node_id)
    return reset
