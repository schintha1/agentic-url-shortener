"""Isolated per-run worktree.

Implement stages patch a copy of the service, not the live tree. Snapshot and
restore cover that copy as well as artifacts, so rollback undoes a real change.
"""

from __future__ import annotations

import hashlib
import shutil
from difflib import unified_diff
from pathlib import Path

from app.errors import AppError
from app.orchestrator.store import artifacts_dir, run_dir

IGNORE_NAMES = {
    ".venv",
    "runs",
    "data",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".DS_Store",
}


def workspace_dir(runs_dir: str, run_id: str) -> Path:
    return run_dir(runs_dir, run_id) / "workspace"


def seed_dir(runs_dir: str, run_id: str) -> Path:
    return run_dir(runs_dir, run_id) / "seed"


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORE_NAMES or name.endswith(".pyc")}


def seed_workspace(runs_dir: str, run_id: str, source_root: Path) -> Path:
    """Copy app/, tests/, and pyproject.toml into the run workspace and seed tree."""

    dest = workspace_dir(runs_dir, run_id)
    if dest.exists() and (dest / "app").exists():
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    app_src = source_root / "app"
    tests_src = source_root / "tests"
    if not app_src.exists() or not tests_src.exists():
        raise AppError(500, "workspace_seed_failed", "Cannot seed workspace: source tree missing")
    shutil.copytree(app_src, dest / "app", ignore=_ignore, dirs_exist_ok=True)
    shutil.copytree(tests_src, dest / "tests", ignore=_ignore, dirs_exist_ok=True)
    pyproject = source_root / "pyproject.toml"
    if pyproject.exists():
        shutil.copy2(pyproject, dest / "pyproject.toml")
    seed = seed_dir(runs_dir, run_id)
    if seed.exists():
        shutil.rmtree(seed)
    shutil.copytree(dest, seed, ignore=_ignore)
    return dest


def ensure_workspace(runs_dir: str, run_id: str, source_root: Path) -> Path:
    return seed_workspace(runs_dir, run_id, source_root)


def workspace_fingerprint(root: Path) -> str:
    """Stable digest of tracked files, used to prove rollback restored the tree."""

    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()[:32]
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts or relative.endswith(".pyc"):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:32]


def write_unified_diff(runs_dir: str, run_id: str) -> Path | None:
    """Write artifacts/change.patch from seed vs current workspace. Empty if no delta."""

    seed = seed_dir(runs_dir, run_id)
    current = workspace_dir(runs_dir, run_id)
    if not seed.exists() or not current.exists():
        return None
    lines: list[str] = []
    seed_files = {
        p.relative_to(seed).as_posix()
        for p in seed.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    current_files = {
        p.relative_to(current).as_posix()
        for p in current.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    for relative in sorted(seed_files | current_files):
        left = seed / relative
        right = current / relative
        try:
            left_text = (
                left.read_text(encoding="utf-8").splitlines(keepends=True) if left.exists() else []
            )
            right_text = (
                right.read_text(encoding="utf-8").splitlines(keepends=True) if right.exists() else []
            )
        except UnicodeDecodeError:
            continue
        if left_text == right_text:
            continue
        lines.extend(
            unified_diff(
                left_text,
                right_text,
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
    dest = artifacts_dir(runs_dir, run_id) / "change.patch"
    dest.write_text("".join(lines), encoding="utf-8")
    return dest


def delete_artifact_files(runs_dir: str, run_id: str, names: list[str]) -> None:
    directory = artifacts_dir(runs_dir, run_id)
    for name in names:
        path = directory / name
        if path.is_file():
            path.unlink()


def snapshot_exists(runs_dir: str, run_id: str) -> bool:
    return (run_dir(runs_dir, run_id) / "snapshot").exists()
