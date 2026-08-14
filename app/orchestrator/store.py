import json
import os
import re
from pathlib import Path

from app.errors import AppError
from app.orchestrator.models import AuditEvent, RunState

RUN_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def assert_safe_run_id(run_id: str) -> None:
    if not RUN_ID_RE.match(run_id):
        raise AppError(400, "invalid_run_id", "Invalid run id")


def run_dir(runs_dir: str, run_id: str) -> Path:
    assert_safe_run_id(run_id)
    return Path(runs_dir) / run_id


def artifacts_dir(runs_dir: str, run_id: str) -> Path:
    path = run_dir(runs_dir, run_id) / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_run(runs_dir: str, run: RunState) -> None:
    directory = run_dir(runs_dir, run.id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "artifacts").mkdir(exist_ok=True)
    target = directory / "run.json"
    tmp = directory / "run.json.tmp"
    tmp.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, target)


def load_run(runs_dir: str, run_id: str) -> RunState:
    path = run_dir(runs_dir, run_id) / "run.json"
    if not path.exists():
        raise AppError(404, "run_not_found", "Run not found")
    return RunState.model_validate_json(path.read_text(encoding="utf-8"))


def append_audit(runs_dir: str, run_id: str, event: AuditEvent) -> None:
    directory = run_dir(runs_dir, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "audit.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(event.model_dump_json() + "\n")


def read_audit(runs_dir: str, run_id: str) -> list[AuditEvent]:
    path = run_dir(runs_dir, run_id) / "audit.jsonl"
    if not path.exists():
        return []
    events: list[AuditEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(AuditEvent.model_validate(json.loads(line)))
    return events


def snapshot_artifacts(runs_dir: str, run_id: str) -> None:
    import shutil

    source = artifacts_dir(runs_dir, run_id)
    dest = run_dir(runs_dir, run_id) / "snapshot"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)


def restore_artifacts(runs_dir: str, run_id: str) -> None:
    import shutil

    dest = artifacts_dir(runs_dir, run_id)
    source = run_dir(runs_dir, run_id) / "snapshot"
    if not source.exists():
        return
    shutil.rmtree(dest)
    shutil.copytree(source, dest)
