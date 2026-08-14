"""Reliability metrics.

Computed from disk so they survive a restart, and cached in a rollup so the
endpoint does not rescan every run on every request. `recompute` exists so the
cache can always be checked against the truth it summarises.
"""

import json
import os
from pathlib import Path

from pydantic import BaseModel

from app.orchestrator.models import AuditEvent, NodeStatus, RunState, RunStatus
from app.orchestrator.store import list_run_ids, load_run, read_audit

ROLLUP_FILE = "metrics.json"


class Metrics(BaseModel):
    runs_total: int = 0
    succeeded: int = 0
    failed: int = 0
    success_rate: float = 0.0
    retry_count: int = 0
    rollback_count: int = 0
    fallback_count: int = 0
    retry_rate: float = 0.0
    rollback_rate: float = 0.0
    fallback_rate: float = 0.0
    e2e_latency_ms_avg: float = 0.0
    mttr_ms: float = 0.0
    incidents: int = 0


def _incident_recovery_times(events: list[AuditEvent]) -> list[float]:
    """Pair each node failure with its next success on the same node."""

    failed_at: dict[str, float] = {}
    durations: list[float] = []
    for event in events:
        if event.node_id is None:
            continue
        ts = event.ts.timestamp() * 1000
        if event.to_status == NodeStatus.FAILED.value:
            failed_at.setdefault(event.node_id, ts)
        elif event.to_status == NodeStatus.SUCCEEDED.value and event.node_id in failed_at:
            durations.append(ts - failed_at.pop(event.node_id))
    return durations


def compute(runs_dir: str) -> Metrics:
    """Full scan across every run on disk."""

    run_ids = list_run_ids(runs_dir)
    runs: list[RunState] = [load_run(runs_dir, run_id) for run_id in run_ids]
    total = len(runs)
    if total == 0:
        return Metrics()

    succeeded = sum(1 for run in runs if run.status == RunStatus.SUCCEEDED)
    failed = sum(
        1
        for run in runs
        if run.status in {RunStatus.FAILED, RunStatus.ROLLED_BACK}
    )
    retries = sum(run.retry_count for run in runs)
    rollbacks = sum(run.rollback_count for run in runs)
    fallbacks = sum(run.fallback_count for run in runs)
    latencies = [
        (run.updated_at - run.created_at).total_seconds() * 1000 for run in runs
    ]
    recoveries: list[float] = []
    for run_id in run_ids:
        recoveries.extend(_incident_recovery_times(read_audit(runs_dir, run_id)))

    return Metrics(
        runs_total=total,
        succeeded=succeeded,
        failed=failed,
        success_rate=succeeded / total,
        retry_count=retries,
        rollback_count=rollbacks,
        fallback_count=fallbacks,
        retry_rate=retries / total,
        rollback_rate=rollbacks / total,
        fallback_rate=fallbacks / total,
        e2e_latency_ms_avg=sum(latencies) / len(latencies),
        mttr_ms=(sum(recoveries) / len(recoveries)) if recoveries else 0.0,
        incidents=len(recoveries),
    )


def _rollup_path(runs_dir: str) -> Path:
    return Path(runs_dir) / ROLLUP_FILE


def write_rollup(runs_dir: str, metrics: Metrics) -> None:
    root = Path(runs_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = _rollup_path(runs_dir)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(metrics.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, target)


def read_rollup(runs_dir: str) -> Metrics | None:
    path = _rollup_path(runs_dir)
    if not path.exists():
        return None
    try:
        return Metrics.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, json.JSONDecodeError):
        return None


def refresh(runs_dir: str) -> Metrics:
    """Recompute and persist the rollup. Called when a run reaches a terminal state."""

    metrics = compute(runs_dir)
    write_rollup(runs_dir, metrics)
    return metrics


def current(runs_dir: str, recompute: bool = False) -> Metrics:
    if recompute:
        return refresh(runs_dir)
    cached = read_rollup(runs_dir)
    if cached is None:
        return refresh(runs_dir)
    return cached
