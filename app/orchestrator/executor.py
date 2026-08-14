import asyncio

from app.errors import AppError
from app.orchestrator.agents import run_stage
from app.orchestrator.models import (
    AuditEvent,
    Autonomy,
    NodeState,
    NodeStatus,
    RunState,
    RunStatus,
    utcnow,
)
from app.orchestrator.store import append_audit, save_run

STAGE_ERRORS = (RuntimeError, OSError, ValueError, AppError)


def _ready(run: RunState) -> list[NodeState]:
    ready: list[NodeState] = []
    for node in run.nodes.values():
        if node.status != NodeStatus.PENDING:
            continue
        deps_ok = True
        for dep_id in node.spec.requires:
            dep = run.nodes[dep_id]
            if dep.status != NodeStatus.SUCCEEDED and not (
                dep.status == NodeStatus.FAILED and dep.fallback_applied
            ):
                deps_ok = False
                break
        if deps_ok:
            ready.append(node)
    return ready


def _audit(
    runs_dir: str,
    run: RunState,
    node_id: str | None,
    from_status: str | None,
    to_status: str | None,
    actor: str,
    message: str,
    extra: dict[str, str] | None = None,
) -> None:
    append_audit(
        runs_dir,
        run.id,
        AuditEvent(
            ts=utcnow(),
            node_id=node_id,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            message=message,
            extra=extra or {},
        ),
    )


def _set_status(run: RunState, node: NodeState, status: NodeStatus) -> None:
    node.status = status
    if status in {NodeStatus.SUCCEEDED, NodeStatus.FAILED, NodeStatus.STOPPED, NodeStatus.ROLLED_BACK}:
        node.finished_at = utcnow()
    run.updated_at = utcnow()


def _fail_node(runs_dir: str, run: RunState, node: NodeState, exc: BaseException) -> None:
    node.error = str(exc)
    if run.first_failure_at is None:
        run.first_failure_at = utcnow()
    _set_status(run, node, NodeStatus.FAILED)
    _audit(
        runs_dir,
        run,
        node.spec.id,
        NodeStatus.RUNNING.value,
        NodeStatus.FAILED.value,
        "system",
        "node failed",
        extra={"error_type": type(exc).__name__},
    )


def _succeed_node(runs_dir: str, run: RunState, node: NodeState) -> None:
    _set_status(run, node, NodeStatus.SUCCEEDED)
    _audit(
        runs_dir,
        run,
        node.spec.id,
        NodeStatus.RUNNING.value,
        NodeStatus.SUCCEEDED.value,
        "agent",
        "node succeeded",
    )


def _run_stage_sync(runs_dir: str, run: RunState, node: NodeState) -> None:
    if run.inject_failure_node == node.spec.id and run.inject_failure_remaining > 0:
        run.inject_failure_remaining -= 1
        raise RuntimeError("injected failure")
    run_stage(node.spec.stage, run, runs_dir)


async def _execute_ready(runs_dir: str, run: RunState, ready: list[NodeState]) -> None:
    for node in ready:
        node.started_at = utcnow()
        node.attempts += 1
        _set_status(run, node, NodeStatus.RUNNING)
        _audit(
            runs_dir,
            run,
            node.spec.id,
            NodeStatus.PENDING.value,
            NodeStatus.RUNNING.value,
            "agent",
            "node started",
        )
    save_run(runs_dir, run)

    async def work(node: NodeState) -> tuple[NodeState, BaseException | None]:
        try:
            await asyncio.to_thread(_run_stage_sync, runs_dir, run, node)
            return node, None
        except STAGE_ERRORS as exc:
            return node, exc

    results = await asyncio.gather(*[work(node) for node in ready], return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            continue
        node, exc = result
        if exc is None:
            _succeed_node(runs_dir, run, node)
        else:
            _fail_node(runs_dir, run, node, exc)
    save_run(runs_dir, run)


def _finalize(run: RunState) -> None:
    statuses = {n.status for n in run.nodes.values()}
    if NodeStatus.GATE_WAIT in statuses:
        run.status = RunStatus.GATE_WAIT
        return
    if run.stop_requested or NodeStatus.STOPPED in statuses:
        run.status = RunStatus.STOPPED
        return
    if NodeStatus.ROLLED_BACK in statuses:
        run.status = RunStatus.ROLLED_BACK
        return
    if NodeStatus.FAILED in statuses or NodeStatus.PENDING in statuses:
        run.status = RunStatus.FAILED
        return
    run.status = RunStatus.SUCCEEDED


async def advance(runs_dir: str, run: RunState) -> RunState:
    """Execute ready nodes until blocked (HITL) or the run is terminal."""

    while run.status == RunStatus.RUNNING:
        if run.stop_requested:
            for node in run.nodes.values():
                if node.status == NodeStatus.PENDING:
                    _set_status(run, node, NodeStatus.STOPPED)
            _finalize(run)
            save_run(runs_dir, run)
            return run
        ready = _ready(run)
        if not ready:
            _finalize(run)
            save_run(runs_dir, run)
            return run
        gated = [
            node
            for node in ready
            if node.spec.autonomy == Autonomy.HUMAN_REQUIRED and not run.auto_approve
        ]
        if gated:
            for node in gated:
                _set_status(run, node, NodeStatus.GATE_WAIT)
                _audit(
                    runs_dir,
                    run,
                    node.spec.id,
                    NodeStatus.PENDING.value,
                    NodeStatus.GATE_WAIT.value,
                    "system",
                    "waiting for human approval",
                )
            run.status = RunStatus.GATE_WAIT
            save_run(runs_dir, run)
            return run
        await _execute_ready(runs_dir, run, ready)
    save_run(runs_dir, run)
    return run
