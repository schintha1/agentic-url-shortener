"""Run the three required scenarios in process.

Drives the planner and executor directly rather than through an HTTP client, so
the shipped package carries no test-only dependency. Writes a machine-readable
summary to stdout; diagnostics go to stderr.
"""

import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config import Settings
from app.orchestrator.context import REPO_ROOT
from app.orchestrator.executor import advance
from app.orchestrator.models import (
    AuditEvent,
    Autonomy,
    NodeState,
    NodeStatus,
    RunState,
    RunStatus,
    ScenarioType,
    utcnow,
)
from app.orchestrator.planner import plan, replan
from app.orchestrator.store import append_audit, load_run, save_run
from app.orchestrator.workspace import seed_workspace

SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios"


class ScenarioPack(BaseModel):
    name: str
    requirement: str
    default_decision: dict[str, object] = Field(default_factory=dict)


def load_pack(name: str) -> ScenarioPack:
    path = SCENARIO_DIR / f"{name}.json"
    return ScenarioPack.model_validate_json(path.read_text(encoding="utf-8"))


def _new_run(pack: ScenarioPack, settings: Settings, auto_approve: bool) -> RunState:
    specs = plan(pack.name, pack.requirement)
    now = utcnow()
    return RunState(
        id=str(uuid4()),
        scenario=ScenarioType(pack.name),
        requirement=pack.requirement,
        nodes={spec.id: NodeState(spec=spec) for spec in specs},
        auto_approve=auto_approve,
        domain_test_target=settings.domain_test_target,
        created_at=now,
        updated_at=now,
    )


def _approve(runs_dir: str, run: RunState, decision: dict[str, object]) -> RunState:
    """Approve every gate the run is currently waiting on, as a human would."""

    waiting = [node for node in run.nodes.values() if node.status == NodeStatus.GATE_WAIT]
    for node in waiting:
        append_audit(
            runs_dir,
            run.id,
            AuditEvent(
                ts=utcnow(),
                node_id=node.spec.id,
                from_status=NodeStatus.GATE_WAIT.value,
                to_status=NodeStatus.PENDING.value,
                actor="human",
                message="approved by demo operator",
            ),
        )
        if run.scenario is ScenarioType.AMBIGUOUS and node.spec.stage in {
            "understand",
            "confirm_scope",
        }:
            run.assumptions = {k: str(v) for k, v in decision.items()}
            planned = {spec.id: spec for spec in replan(run, decision)}
            preserved = {
                node_id
                for node_id, existing in run.nodes.items()
                if node_id not in planned and existing.spec.stage == "apply_assumptions"
            }
            for node_id in list(run.nodes):
                if node_id not in planned and node_id not in preserved:
                    del run.nodes[node_id]
            for spec in planned.values():
                existing = run.nodes.get(spec.id)
                if existing is None:
                    run.nodes[spec.id] = NodeState(spec=spec)
                else:
                    existing.spec = spec
        node.spec = node.spec.model_copy(update={"autonomy": Autonomy.AUTO})
        node.change_controlled = False
        node.status = NodeStatus.PENDING
    run.status = RunStatus.RUNNING
    save_run(runs_dir, run)
    return run


async def _drive(pack: ScenarioPack, settings: Settings, auto_approve: bool) -> dict[str, object]:
    runs_dir = settings.runs_dir
    run = _new_run(pack, settings, auto_approve)
    save_run(runs_dir, run)
    seed_workspace(runs_dir, run.id, REPO_ROOT)
    run = await advance(runs_dir, run)

    approvals = 0
    # A run may gate more than once: an ambiguous requirement first, then release.
    while run.status is RunStatus.GATE_WAIT and approvals < 5:
        run = _approve(runs_dir, run, pack.default_decision)
        run = await advance(runs_dir, run)
        approvals += 1

    persisted = load_run(runs_dir, run.id)
    artifacts = Path(runs_dir) / persisted.id / "artifacts"
    patch = artifacts / "change.patch"
    changed_files: list[str] = []
    for report in sorted(artifacts.glob("implementation_*.json")):
        payload = json.loads(report.read_text(encoding="utf-8"))
        changed_files.extend(payload.get("changed_files") or [])
    patch_bytes = patch.read_bytes() if patch.is_file() else b""
    return {
        "status": persisted.status.value,
        "id": persisted.id,
        "approvals": approvals,
        "nodes": sorted(persisted.nodes),
        "gates": sorted(
            node_id
            for node_id, node in persisted.nodes.items()
            if node.spec.autonomy == Autonomy.HUMAN_REQUIRED
        ),
        "changed_files": sorted(set(changed_files)),
        "artifacts": sorted(path.name for path in artifacts.iterdir() if path.is_file()),
        "degraded": sorted(
            node_id for node_id, node in persisted.nodes.items() if node.fallback_applied
        ),
        "patch_bytes": len(patch_bytes),
        "patch_sha": hashlib.sha256(patch_bytes).hexdigest()[:16] if patch_bytes else "",
    }


async def run_demo_async(runs_dir: str, database_url: str) -> dict[str, object]:
    settings = Settings(runs_dir=runs_dir, database_url=database_url, allow_private_targets=True)
    Path(runs_dir).mkdir(parents=True, exist_ok=True)
    results: dict[str, object] = {}
    for name, auto_approve in (("greenfield", True), ("brownfield", True), ("ambiguous", False)):
        pack = load_pack(name)
        results[name] = await _drive(pack, settings, auto_approve)
    return results


def run_demo(runs_dir: str, database_url: str) -> dict[str, object]:
    return asyncio.run(run_demo_async(runs_dir, database_url))


def main() -> int:
    dest = Path("runs") / "demo-last"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    summary = run_demo(str(dest / "runs"), f"sqlite:///{dest / 'demo.db'}")
    print(json.dumps(summary, indent=2))
    failures = [
        name
        for name, result in summary.items()
        if not isinstance(result, dict) or result.get("status") != "succeeded"
    ]
    if failures:
        print(f"scenarios did not succeed: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
