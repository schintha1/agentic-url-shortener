import asyncio

from app.errors import AppError
from app.orchestrator.agents import run_stage
from app.orchestrator.artifacts import validate_declared
from app.orchestrator.context import REPO_ROOT, StageContext
from app.orchestrator.invalidation import compute_input_hash
from app.orchestrator.metrics import refresh as refresh_metrics
from app.orchestrator.models import (
    AuditEvent,
    Autonomy,
    NodeState,
    NodeStatus,
    RunState,
    RunStatus,
    utcnow,
)
from app.orchestrator.policy import (
    check_artifacts,
    check_release_compliance,
    requires_change_control,
)
from app.orchestrator.store import (
    append_audit,
    artifacts_dir,
    restore_artifacts,
    save_run,
    snapshot_artifacts,
    stop_was_requested,
)
from app.orchestrator.workspace import ensure_workspace

STAGE_ERRORS = (Exception,)


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
    repo_root = ensure_workspace(runs_dir, run.id, REPO_ROOT)
    ctx = StageContext(node, run, runs_dir, repo_root=repo_root)
    # Record what this node consumed so a later change can invalidate it.
    node.input_hash = compute_input_hash(run, node, ctx.directory)
    if node.spec.stage == "release_prepare":
        # Compliance pack: the release gate needs its evidence on disk.
        missing = check_release_compliance(ctx.directory)
        if missing:
            raise AppError(
                422,
                "compliance_violation",
                f"Release evidence missing: {', '.join(missing)}",
            )
    run_stage(ctx)
    # Exit gate: declared artifacts must exist, validate, and pass policy.
    validate_declared(ctx.directory, node.spec.produces)
    check_artifacts(artifacts_dir(runs_dir, run.id), only=node.spec.produces)
    if run.inject_failure_node == node.spec.id and run.inject_failure_remaining > 0:
        run.inject_failure_remaining -= 1
        raise RuntimeError("injected failure")


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
    for node, result in zip(ready, results, strict=True):
        if isinstance(result, BaseException):
            _fail_node(runs_dir, run, node, result)
            continue
        finished, exc = result
        if exc is None:
            _succeed_node(runs_dir, run, finished)
        else:
            _fail_node(runs_dir, run, finished, exc)
    save_run(runs_dir, run)


def _apply_change_control(runs_dir: str, run: RunState, ready: list[NodeState]) -> None:
    """Withdraw self-approval when an upstream change is too high-impact.

    `auto_approve` is an operator convenience; it must not let an agent release a
    change to authentication, data retention, or a destructive endpoint.
    """

    for node in ready:
        if node.spec.autonomy != Autonomy.HUMAN_REQUIRED or node.change_controlled:
            continue
        needed, reason = requires_change_control(artifacts_dir(runs_dir, run.id))
        if not needed:
            continue
        node.change_controlled = True
        _audit(
            runs_dir,
            run,
            node.spec.id,
            None,
            None,
            "system",
            "change control withdrew auto-approval",
            extra={"reason": reason},
        )


def _finalize(run: RunState) -> None:
    statuses = {n.status for n in run.nodes.values()}
    if run.stop_requested or NodeStatus.STOPPED in statuses:
        run.status = RunStatus.STOPPED
        return
    if NodeStatus.GATE_WAIT in statuses:
        run.status = RunStatus.GATE_WAIT
        return
    if NodeStatus.ROLLED_BACK in statuses:
        run.status = RunStatus.ROLLED_BACK
        return
    # A failed optional node with a fallback applied is a degradation, not a failure.
    blocking_failure = any(
        node.status == NodeStatus.FAILED and not node.fallback_applied
        for node in run.nodes.values()
    )
    if blocking_failure or NodeStatus.PENDING in statuses:
        run.status = RunStatus.FAILED
        return
    run.status = RunStatus.SUCCEEDED


TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.ROLLED_BACK,
    RunStatus.STOPPED,
}


async def advance(runs_dir: str, run: RunState) -> RunState:
    """Execute ready nodes until blocked (HITL) or the run is terminal."""

    result = await _advance_loop(runs_dir, run)
    if result.status in TERMINAL_RUN_STATUSES:
        # Keep the rollup honest without rescanning every run on every read.
        refresh_metrics(runs_dir)
    return result


def _apply_stop(runs_dir: str, run: RunState) -> RunState:
    run.stop_requested = True
    for node in run.nodes.values():
        if node.status in {NodeStatus.PENDING, NodeStatus.GATE_WAIT}:
            _set_status(run, node, NodeStatus.STOPPED)
    _finalize(run)
    save_run(runs_dir, run)
    return run


async def _advance_loop(runs_dir: str, run: RunState) -> RunState:
    if run.stop_requested or stop_was_requested(run.id):
        return _apply_stop(runs_dir, run)
    while run.status == RunStatus.RUNNING:
        if run.stop_requested or stop_was_requested(run.id):
            return _apply_stop(runs_dir, run)
        ready = _ready(run)
        if not ready:
            _finalize(run)
            save_run(runs_dir, run)
            return run
        _apply_change_control(runs_dir, run, ready)
        gated = [
            node
            for node in ready
            if node.spec.autonomy == Autonomy.HUMAN_REQUIRED
            and not (run.auto_approve and not node.change_controlled)
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
        if any(node.spec.stage == "implement" for node in ready):
            snapshot_artifacts(runs_dir, run.id)
        await _execute_ready(runs_dir, run, ready)
        retried = False
        for node in ready:
            if node.status != NodeStatus.FAILED:
                continue
            if node.attempts < node.spec.max_retries:
                run.retry_count += 1
                node.status = NodeStatus.RETRYING
                _audit(
                    runs_dir,
                    run,
                    node.spec.id,
                    NodeStatus.FAILED.value,
                    NodeStatus.RETRYING.value,
                    "system",
                    "retry scheduled",
                )
                node.status = NodeStatus.PENDING
                node.finished_at = None
                node.error = None
                retried = True
            elif node.spec.optional:
                # Fallback: an optional gate degrades the run rather than failing it.
                node.fallback_applied = True
                run.fallback_count += 1
                _audit(
                    runs_dir,
                    run,
                    node.spec.id,
                    NodeStatus.FAILED.value,
                    NodeStatus.FAILED.value,
                    "system",
                    "fallback_applied: optional stage degraded, run continues",
                    extra={"stage": node.spec.stage},
                )
            elif node.spec.stage == "implement":
                restore_artifacts(runs_dir, run.id)
                _set_status(run, node, NodeStatus.ROLLED_BACK)
                run.rollback_count += 1
                _audit(
                    runs_dir,
                    run,
                    node.spec.id,
                    NodeStatus.FAILED.value,
                    NodeStatus.ROLLED_BACK.value,
                    "system",
                    "implement rolled back",
                )
        if retried:
            run.status = RunStatus.RUNNING
            save_run(runs_dir, run)
            await asyncio.sleep(0.01)
            continue
        if run.first_failure_at and any(n.status == NodeStatus.SUCCEEDED and n.attempts > 1 for n in ready):
            run.recovered_at = utcnow()
    save_run(runs_dir, run)
    return run
