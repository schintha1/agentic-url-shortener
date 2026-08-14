import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from app.orchestrator.artifacts import RequirementBrief
from app.orchestrator.context import StageContext
from app.orchestrator.models import NodeState, RunState, ScenarioType, utcnow
from app.orchestrator.planner import plan
from app.orchestrator.store import save_run


def _build_run(runs_dir: str, requirement: str = "Add rate limiting") -> RunState:
    specs = plan("greenfield", requirement)
    run = RunState(
        id="11111111-2222-3333-4444-555555555555",
        scenario=ScenarioType.GREENFIELD,
        requirement=requirement,
        nodes={spec.id: NodeState(spec=spec) for spec in specs},
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    save_run(runs_dir, run)
    return run


def test_readable_set_follows_transitive_dependencies(settings) -> None:
    run = _build_run(settings.runs_dir)
    ctx = StageContext(run.nodes["design"], run, settings.runs_dir)
    readable = ctx.readable()
    assert "requirement_brief.json" in readable
    assert "task_dag.json" in readable
    assert "test_report.json" not in readable


def test_read_of_undeclared_artifact_is_refused(settings) -> None:
    run = _build_run(settings.runs_dir)
    ctx = StageContext(run.nodes["understand"], run, settings.runs_dir)
    with pytest.raises(AppError) as exc:
        ctx.read("test_report.json")
    assert exc.value.code == "undeclared_dependency"


def test_write_of_undeclared_artifact_is_refused(settings) -> None:
    run = _build_run(settings.runs_dir)
    ctx = StageContext(run.nodes["understand"], run, settings.runs_dir)
    with pytest.raises(AppError) as exc:
        ctx.write("test_report.json", "nope")
    assert exc.value.code == "undeclared_artifact"


def test_round_trip_through_the_bus(settings) -> None:
    run = _build_run(settings.runs_dir)
    upstream = StageContext(run.nodes["understand"], run, settings.runs_dir)
    upstream.write(
        "requirement_brief.json",
        RequirementBrief(intent="Add rate limiting", acceptance_criteria=["429 on excess"]),
    )
    downstream = StageContext(run.nodes["decompose"], run, settings.runs_dir)
    brief = downstream.read("requirement_brief.json")
    assert isinstance(brief, RequirementBrief)
    assert brief.intent == "Add rate limiting"


def test_analysis_and_codebase_are_available(settings) -> None:
    run = _build_run(settings.runs_dir)
    ctx = StageContext(run.nodes["design"], run, settings.runs_dir)
    assert ctx.analysis.capabilities
    assert ctx.codebase.endpoint_labels()


def test_design_artifact_carries_requirement_specific_content(client: TestClient) -> None:
    """Proof that data crossed a stage boundary rather than being templated."""

    from pathlib import Path

    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Add caching for redirects",
            "auto_approve": True,
        },
    )
    run_id = response.json()["id"]
    design = (
        Path(client.app.state.settings.runs_dir) / run_id / "artifacts" / "design.md"
    ).read_text(encoding="utf-8")
    assert "caching" in design
    assert "Add caching for redirects" in design
    assert "Hot reads are served without a database round trip" in design


def test_document_varies_with_the_requirement(client: TestClient) -> None:
    from pathlib import Path

    runs_dir = Path(client.app.state.settings.runs_dir)
    first = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Add caching", "auto_approve": True},
    ).json()["id"]
    second = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Add API key auth", "auto_approve": True},
    ).json()["id"]
    first_doc = (runs_dir / first / "artifacts" / "document.md").read_text(encoding="utf-8")
    second_doc = (runs_dir / second / "artifacts" / "document.md").read_text(encoding="utf-8")
    assert "caching" in first_doc
    assert "auth" in second_doc
    assert first_doc != second_doc
