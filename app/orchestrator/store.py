"""Filesystem run store.

Run state is written atomically. Audit events are append-only with a hash chain.
A per-run thread lock plus an OS file lock serialise writers in-process and
across processes, including on Windows.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.errors import AppError
from app.orchestrator.models import AuditEvent, RunState

RUN_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_STOP_REQUESTS: set[str] = set()
_PROCESSES: dict[str, object] = {}
_PROCESSES_GUARD = threading.Lock()


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


def _thread_lock(run_id: str) -> threading.Lock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(run_id, threading.Lock())


def _lock_file(handle: object) -> str:
    """Exclusive lock the file; POSIX flock or Windows msvcrt."""

    fileno = handle.fileno()  # type: ignore[attr-defined]
    try:
        import fcntl

        fcntl.flock(fileno, fcntl.LOCK_EX)
        return "fcntl"
    except ImportError:
        import msvcrt

        handle.write(" ")  # type: ignore[attr-defined]
        handle.flush()  # type: ignore[attr-defined]
        handle.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(fileno, msvcrt.LK_LOCK, 1)
        return "msvcrt"


def _unlock_file(handle: object, kind: str) -> None:
    fileno = handle.fileno()  # type: ignore[attr-defined]
    if kind == "fcntl":
        import fcntl

        fcntl.flock(fileno, fcntl.LOCK_UN)
        return
    import msvcrt

    handle.seek(0)  # type: ignore[attr-defined]
    msvcrt.locking(fileno, msvcrt.LK_UNLCK, 1)


@contextmanager
def run_lock(runs_dir: str, run_id: str) -> Iterator[None]:
    """Serialise read-modify-write cycles on one run across threads and processes."""

    directory = run_dir(runs_dir, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".lock"
    with _thread_lock(run_id), lock_path.open("a+", encoding="utf-8") as handle:
        kind = _lock_file(handle)
        try:
            yield
        finally:
            _unlock_file(handle, kind)


def request_stop(run_id: str) -> None:
    _STOP_REQUESTS.add(run_id)


def clear_stop(run_id: str) -> None:
    _STOP_REQUESTS.discard(run_id)


def stop_was_requested(run_id: str) -> bool:
    return run_id in _STOP_REQUESTS


def register_process(run_id: str, process: object) -> None:
    with _PROCESSES_GUARD:
        _PROCESSES[run_id] = process


def unregister_process(run_id: str) -> None:
    with _PROCESSES_GUARD:
        _PROCESSES.pop(run_id, None)


def kill_run_process(run_id: str) -> None:
    with _PROCESSES_GUARD:
        process = _PROCESSES.get(run_id)
    if process is None:
        return
    kill = getattr(process, "kill", None)
    if callable(kill):
        kill()


def _on_disk_version(directory: Path) -> int | None:
    path = directory / "run.json"
    if not path.exists():
        return None
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("version", 0))
    except (ValueError, OSError):
        return None


def save_run(runs_dir: str, run: RunState, check_version: bool = True) -> None:
    """Persist a run atomically, refusing a write that would clobber a newer one."""

    directory = run_dir(runs_dir, run.id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "artifacts").mkdir(exist_ok=True)
    if check_version:
        current = _on_disk_version(directory)
        if current is not None and current != run.version:
            raise AppError(
                409,
                "run_conflict",
                "Run was modified by another writer; reload and retry",
            )
    run.version += 1
    target = directory / "run.json"
    tmp = directory / f"run.json.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, target)


def load_run(runs_dir: str, run_id: str) -> RunState:
    path = run_dir(runs_dir, run_id) / "run.json"
    if not path.exists():
        raise AppError(404, "run_not_found", "Run not found")
    return RunState.model_validate_json(path.read_text(encoding="utf-8"))


def _event_hash(event: AuditEvent) -> str:
    payload = event.model_dump_json()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def append_audit(runs_dir: str, run_id: str, event: AuditEvent) -> None:
    directory = run_dir(runs_dir, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "audit.jsonl"
    previous = read_audit(runs_dir, run_id)
    event.seq = len(previous) + 1
    event.prev_hash = _event_hash(previous[-1]) if previous else ""
    with path.open("a", encoding="utf-8") as handle:
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


def clear_snapshot(runs_dir: str, run_id: str) -> None:
    dest = run_dir(runs_dir, run_id) / "snapshot"
    if dest.exists():
        shutil.rmtree(dest)


def snapshot_artifacts(runs_dir: str, run_id: str) -> None:
    """Snapshot artifacts and workspace once per implement batch."""

    dest = run_dir(runs_dir, run_id) / "snapshot"
    if dest.exists():
        return
    dest.mkdir(parents=True)
    source_art = artifacts_dir(runs_dir, run_id)
    shutil.copytree(source_art, dest / "artifacts", dirs_exist_ok=True)
    from app.orchestrator.workspace import workspace_dir

    workspace = workspace_dir(runs_dir, run_id)
    if workspace.exists():
        shutil.copytree(workspace, dest / "workspace", dirs_exist_ok=True)


def restore_artifacts(runs_dir: str, run_id: str) -> None:
    dest_art = artifacts_dir(runs_dir, run_id)
    source = run_dir(runs_dir, run_id) / "snapshot"
    if not source.exists():
        return
    art_src = source / "artifacts" if (source / "artifacts").exists() else source
    if dest_art.exists():
        shutil.rmtree(dest_art)
    shutil.copytree(art_src, dest_art)
    from app.orchestrator.workspace import workspace_dir

    snap_ws = source / "workspace"
    workspace = workspace_dir(runs_dir, run_id)
    if snap_ws.exists():
        if workspace.exists():
            shutil.rmtree(workspace)
        shutil.copytree(snap_ws, workspace)


def list_run_ids(runs_dir: str) -> list[str]:
    root = Path(runs_dir)
    if not root.exists():
        return []
    return sorted(
        child.name
        for child in root.iterdir()
        if child.is_dir() and (child / "run.json").exists() and RUN_ID_RE.match(child.name)
    )
