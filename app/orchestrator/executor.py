from app.errors import AppError
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

PARALLEL = False


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


async def _execute_node(runs_dir: str, run: RunState, node: NodeState) -> None:
    from app.orchestrator.agents import run_stage

    node.started_at = utcnow()
    node.attempts += 1
    _set_status(run, node, NodeStatus.RUNNING)
    _audit(runs_dir, run, node.spec.id, NodeStatus.PENDING.value, NodeStatus.RUNNING.value, "agent", "node started")
    save_run(runs_dir, run)
    try:
        if run.inject_failure_node == node.spec.id and run.inject_failure_remaining > 0:
            run.inject_failure_remaining -= 1
            raise RuntimeError("injected failure")
        run_stage(node.spec.stage, run, runs_dir)
    except (RuntimeError, OSError, ValueError, AppError) as exc:
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
        save_run(runs_dir, run)
        return
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
        if PARALLEL:
            import asyncio

            results = await asyncio.gather(
                *[_execute_node(runs_dir, run, node) for node in ready],
                return_exceptions=True,
            )
            for node, result in zip(ready, results, strict=True):
                if isinstance(result, Exception):
                    node.error = str(result)
                    _set_status(run, node, NodeStatus.FAILED)
        else:
            await _execute_node(runs_dir, run, ready[0])
    save_run(runs_dir, run)
    return run
