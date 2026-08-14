import hashlib
import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.errors import AppError
from app.orchestrator.executor import advance
from app.orchestrator.models import (
    AuditEvent,
    Autonomy,
    NodeState,
    NodeStatus,
    RunState,
    RunStatus,
    utcnow,
)
from app.orchestrator.planner import plan, replan
from app.orchestrator.schemas import ApproveRequest, CreateRunRequest
from app.orchestrator.store import append_audit, load_run, read_audit, save_run

router = APIRouter(prefix="/sdlc", tags=["sdlc"])

TERMINAL_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.ROLLED_BACK,
    RunStatus.STOPPED,
}


class MetricsResponse(BaseModel):
    runs_total: int
    success_rate: float
    retry_count: int
    rollback_count: int
    e2e_latency_ms_avg: float
    mttr_ms: float


class TraceEvent(BaseModel):
    ts: str
    node_id: str | None
    from_status: str | None
    to_status: str | None
    actor: str
    message: str
    extra: dict[str, str] = Field(default_factory=dict)


def get_runs_dir(request: Request) -> str:
    return request.app.state.settings.runs_dir  # type: ignore[no-any-return]


def get_default_test_target(request: Request) -> str:
    return request.app.state.settings.domain_test_target  # type: ignore[no-any-return]


RunsDir = Annotated[str, Depends(get_runs_dir)]
DefaultTestTarget = Annotated[str, Depends(get_default_test_target)]


def _decision_hash(decision: dict[str, object]) -> str:
    payload = json.dumps(decision, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@router.post("/runs", summary="Create and execute an SDLC run until blocked")
async def create_run(
    body: CreateRunRequest,
    runs_dir: RunsDir,
    default_test_target: DefaultTestTarget,
) -> RunState:
    try:
        specs = plan(body.scenario.value, body.requirement)
    except ValueError as exc:
        raise AppError(422, "invalid_scenario", str(exc)) from exc
    now = utcnow()
    run = RunState(
        id=str(uuid4()),
        scenario=body.scenario,
        requirement=body.requirement,
        nodes={spec.id: NodeState(spec=spec) for spec in specs},
        auto_approve=body.auto_approve,
        inject_failure_node=body.inject_failure_node,
        inject_failure_remaining=body.inject_failure_count if body.inject_failure_node else 0,
        domain_test_target=body.domain_test_target or default_test_target,
        created_at=now,
        updated_at=now,
    )
    save_run(runs_dir, run)
    return await advance(runs_dir, run)


@router.get("/runs/{run_id}", summary="Get an SDLC run")
def get_run(run_id: str, runs_dir: RunsDir) -> RunState:
    return load_run(runs_dir, run_id)


@router.get("/runs/{run_id}/trace", summary="Audit trace for a run")
def get_trace(run_id: str, runs_dir: RunsDir) -> list[TraceEvent]:
    events = read_audit(runs_dir, run_id)
    return [
        TraceEvent(
            ts=event.ts.isoformat(),
            node_id=event.node_id,
            from_status=event.from_status,
            to_status=event.to_status,
            actor=event.actor,
            message=event.message,
            extra=event.extra,
        )
        for event in events
    ]


@router.post("/runs/{run_id}/approve", summary="Approve a gate_wait node")
async def approve_run(run_id: str, body: ApproveRequest, runs_dir: RunsDir) -> RunState:
    run = load_run(runs_dir, run_id)
    node = run.nodes.get(body.node_id)
    if node is None or node.status != NodeStatus.GATE_WAIT:
        raise AppError(409, "not_waiting", "Node is not waiting for approval")
    from app.orchestrator.models import AuditEvent
    from app.orchestrator.store import append_audit

    append_audit(
        runs_dir,
        run.id,
        AuditEvent(
            ts=utcnow(),
            node_id=node.spec.id,
            from_status=NodeStatus.GATE_WAIT.value,
            to_status=NodeStatus.PENDING.value,
            actor="human",
            message=body.note or "approved",
            extra={"decision_hash": _decision_hash(body.decision)},
        ),
    )
    if body.decision:
        run.assumptions = {k: str(v) for k, v in body.decision.items()}
    if run.scenario.value == "ambiguous" and node.spec.stage == "understand":
        specs = replan(run, body.decision)
        for spec in specs:
            existing = run.nodes.get(spec.id)
            if existing is None:
                run.nodes[spec.id] = NodeState(spec=spec)
            else:
                existing.spec = spec
    node.spec = node.spec.model_copy(update={"autonomy": Autonomy.AUTO})
    node.status = NodeStatus.PENDING
    run.status = RunStatus.RUNNING
    save_run(runs_dir, run)
    return await advance(runs_dir, run)


@router.post("/runs/{run_id}/reject", summary="Reject a gate_wait node")
async def reject_run(run_id: str, body: ApproveRequest, runs_dir: RunsDir) -> RunState:
    run = load_run(runs_dir, run_id)
    node = run.nodes.get(body.node_id)
    if node is None or node.status != NodeStatus.GATE_WAIT:
        raise AppError(409, "not_waiting", "Node is not waiting for approval")
    node.status = NodeStatus.FAILED
    node.error = body.note or "rejected"
    run.status = RunStatus.FAILED
    save_run(runs_dir, run)
    return run


@router.post("/runs/{run_id}/resume", summary="Resume a run interrupted mid-stage")
async def resume_run(run_id: str, runs_dir: RunsDir) -> RunState:
    run = load_run(runs_dir, run_id)
    if run.status in TERMINAL_STATUSES:
        raise AppError(409, "run_terminal", f"Run is {run.status.value} and cannot be resumed")
    reset: list[str] = []
    for node in run.nodes.values():
        if node.status == NodeStatus.RUNNING:
            node.status = NodeStatus.PENDING
            node.finished_at = None
            reset.append(node.spec.id)
    if reset:
        append_audit(
            runs_dir,
            run.id,
            AuditEvent(
                ts=utcnow(),
                from_status=NodeStatus.RUNNING.value,
                to_status=NodeStatus.PENDING.value,
                actor="system",
                message="resume reset interrupted node(s)",
                extra={"nodes": ",".join(reset)},
            ),
        )
    run.stop_requested = False
    run.status = RunStatus.RUNNING
    save_run(runs_dir, run)
    return await advance(runs_dir, run)


@router.post("/runs/{run_id}/stop", summary="Cooperative safe-stop")
async def stop_run(run_id: str, runs_dir: RunsDir) -> RunState:
    run = load_run(runs_dir, run_id)
    run.stop_requested = True
    run.status = RunStatus.RUNNING
    return await advance(runs_dir, run)


@router.get("/metrics", summary="Reliability metrics across runs")
def get_metrics(runs_dir: RunsDir) -> MetricsResponse:
    from pathlib import Path

    runs: list[RunState] = []
    root = Path(runs_dir)
    if root.exists():
        for child in root.iterdir():
            if (child / "run.json").exists():
                runs.append(load_run(runs_dir, child.name))
    total = len(runs)
    successes = [item for item in runs if item.status == RunStatus.SUCCEEDED]
    retries = sum(item.retry_count for item in runs)
    rollbacks = sum(item.rollback_count for item in runs)
    latencies = [
        (item.updated_at - item.created_at).total_seconds() * 1000 for item in runs
    ]
    mttrs = [
        (item.recovered_at - item.first_failure_at).total_seconds() * 1000
        for item in runs
        if item.first_failure_at and item.recovered_at
    ]
    return MetricsResponse(
        runs_total=total,
        success_rate=(len(successes) / total) if total else 0.0,
        retry_count=retries,
        rollback_count=rollbacks,
        e2e_latency_ms_avg=(sum(latencies) / len(latencies)) if latencies else 0.0,
        mttr_ms=(sum(mttrs) / len(mttrs)) if mttrs else 0.0,
    )
