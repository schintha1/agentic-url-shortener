import json
from pathlib import Path

from app.orchestrator.models import RunState
from app.orchestrator.store import artifacts_dir


def run_stage(stage: str, run: RunState, runs_dir: str) -> None:
    """Write a stub artifact for the stage (replaced in S14)."""

    directory = artifacts_dir(runs_dir, run.id)
    path = directory / f"{stage}.json"
    path.write_text(
        json.dumps({"stage": stage, "run_id": run.id, "stub": True}, indent=2),
        encoding="utf-8",
    )
    _ = Path(path)
