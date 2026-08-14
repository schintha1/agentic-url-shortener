"""Run the three required scenarios in process.

Drives the planner and executor directly rather than through an HTTP client, so
the shipped package carries no test-only dependency. Writes a machine-readable
summary to stdout; diagnostics go to stderr.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config import Settings
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
        if run.scenario is ScenarioType.AMBIGUOUS and node.spec.stage == "understand":
            run.assumptions = {k: str(v) for k, v in decision.items()}
            for spec in replan(run, decision):
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
    run = await advance(runs_dir, run)

    approvals = 0
    # A run may gate more than once: an ambiguous requirement first, then release.
    while run.status is RunStatus.GATE_WAIT and approvals < 5:
        run = _approve(runs_dir, run, pack.default_decision)
        run = await advance(runs_dir, run)
        approvals += 1

    persisted = load_run(runs_dir, run.id)
    return {
        "status": persisted.status.value,
        "id": persisted.id,
        "approvals": approvals,
        "nodes": sorted(persisted.nodes),
        "degraded": sorted(
            node_id for node_id, node in persisted.nodes.items() if node.fallback_applied
        ),
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
    with tempfile.TemporaryDirectory() as tmp:
        summary = run_demo(f"{tmp}/runs", f"sqlite:///{tmp}/demo.db")
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
