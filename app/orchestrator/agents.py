import json
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from app.orchestrator.models import RunState
from app.orchestrator.store import artifacts_dir

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_CAP = 8192


class RequirementBrief(BaseModel):
    intent: str
    ambiguities: list[str]
    acceptance_criteria: list[str]


class TaskDag(BaseModel):
    nodes: list[str]
    rationale: str


class ImpactReport(BaseModel):
    modules: list[str]
    apis: list[str]
    tables: list[str]


class ImplementationReport(BaseModel):
    mapping: dict[str, str]


class TestReport(BaseModel):
    exit_code: int
    passed: bool
    output: str


class SecurityReview(BaseModel):
    findings: list[str] = Field(default_factory=list)


def _write(path: Path, payload: BaseModel | str) -> None:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
        return
    path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")


def _understand(run: RunState, directory: Path) -> None:
    ambiguities: list[str] = []
    if run.scenario.value == "ambiguous":
        ambiguities = ["auth model (none vs API key)", "click retention (30 vs 90 days)"]
    brief = RequirementBrief(
        intent=run.requirement.strip(),
        ambiguities=ambiguities,
        acceptance_criteria=[
            "Shorten and redirect work",
            "Analytics and rate limits exist",
            "SDLC run is auditable",
        ],
    )
    _write(directory / "requirement_brief.json", brief)


def _decompose(run: RunState, directory: Path) -> None:
    _write(
        directory / "task_dag.json",
        TaskDag(nodes=list(run.nodes.keys()), rationale="Derived from scenario planner"),
    )


def _impact(_run: RunState, directory: Path) -> None:
    _write(
        directory / "impact.json",
        ImpactReport(
            modules=[
                "app/shortener/routes.py",
                "app/shortener/models.py",
                "app/shortener/rate_limit.py",
            ],
            apis=["POST /v1/shorten", "GET /v1/urls/{code}/stats"],
            tables=["urls", "clicks", "idempotency_keys"],
        ),
    )


def _design(_run: RunState, directory: Path) -> None:
    _write(
        directory / "design.md",
        "# Design\n\nFastAPI shortener with SQLite, Base62 codes, click stats, and HITL release.\n",
    )


def _implement(_run: RunState, directory: Path) -> None:
    _write(
        directory / "implementation_report.json",
        ImplementationReport(
            mapping={
                "shorten": "app/shortener/routes.py",
                "redirect": "app/shortener/routes.py",
                "stats": "app/shortener/service.py",
                "rate_limit": "app/shortener/rate_limit.py",
            }
        ),
    )


def _test(_run: RunState, directory: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_shortener.py", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = (completed.stdout + completed.stderr)[:OUTPUT_CAP]
    report = TestReport(exit_code=completed.returncode, passed=completed.returncode == 0, output=output)
    _write(directory / "test_report.json", report)
    if completed.returncode != 0:
        raise RuntimeError("shortener tests failed")


def _security(_run: RunState, directory: Path) -> None:
    _write(
        directory / "security_review.json",
        SecurityReview(
            findings=[
                "Open redirects limited to http/https",
                "Short codes are enumerable; rate limit mitigates abuse",
                "Referrers/UAs truncated to reduce PII",
            ]
        ),
    )


def _document(_run: RunState, directory: Path) -> None:
    _write(directory / "document.md", "# Run notes\n\nArtifacts produced under this run directory.\n")


def _release(_run: RunState, directory: Path) -> None:
    _write(
        directory / "release_checklist.md",
        "- tests passed\n- policy clean\n- docs present\n- human approval required\n",
    )


def _apply_assumptions(run: RunState, directory: Path) -> None:
    _write(
        directory / "assumptions.json",
        json.dumps({"run_id": run.id, "applied": True}, indent=2),
    )


HANDLERS = {
    "understand": _understand,
    "decompose": _decompose,
    "impact_analysis": _impact,
    "design": _design,
    "implement": _implement,
    "test": _test,
    "security_review": _security,
    "document": _document,
    "release_readiness": _release,
    "apply_assumptions": _apply_assumptions,
}


def run_stage(stage: str, run: RunState, runs_dir: str) -> None:
    """Dispatch a typed stage agent and persist its artifact."""

    directory = artifacts_dir(runs_dir, run.id)
    handler = HANDLERS.get(stage)
    if handler is None:
        (directory / f"{stage}.json").write_text(
            json.dumps({"stage": stage, "run_id": run.id}), encoding="utf-8"
        )
        return
    handler(run, directory)
