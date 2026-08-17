from pathlib import Path

from fastapi.testclient import TestClient


def _completed_run(client: TestClient, requirement: str) -> dict:
    response = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": requirement, "auto_approve": True},
    )
    body = response.json()
    assert body["status"] == "succeeded"
    return body


def test_amend_adds_work_and_invalidates_downstream(client: TestClient) -> None:
    """Re-plan must add the new node, reset what depended on the change, and keep history."""

    before = _completed_run(client, "Add click analytics")
    run_id = before["id"]
    trace_before = client.get(f"/sdlc/runs/{run_id}/trace").json()
    assert before["nodes"]["document"]["status"] == "succeeded"

    amended = client.post(
        f"/sdlc/runs/{run_id}/amend",
        json={"requirement": "Add click analytics and caching", "note": "scope grew"},
    )
    assert amended.status_code == 200
    body = amended.json()

    # New work exists.
    assert "implement_caching" in body["nodes"]
    assert "implement_analytics" in body["nodes"]

    assert body["status"] == "succeeded"
    assert body["nodes"]["implement_analytics"]["status"] == "succeeded"
    assert "implement_caching" in body["nodes"]

    # History survived the re-plan.
    trace_after = client.get(f"/sdlc/runs/{run_id}/trace").json()
    assert len(trace_after) > len(trace_before)
    assert trace_after[: len(trace_before)] == trace_before
    assert any("requirement amended" in event["message"] for event in trace_after)


def test_amend_records_what_changed(client: TestClient) -> None:
    run_id = _completed_run(client, "Add click analytics")["id"]
    client.post(
        f"/sdlc/runs/{run_id}/amend",
        json={"requirement": "Add click analytics and caching"},
    )
    trace = client.get(f"/sdlc/runs/{run_id}/trace").json()
    amend_event = next(e for e in trace if "amended" in e["message"])
    assert "implement_caching" in amend_event["extra"]["added"]
    assert amend_event["extra"]["previous_requirement_hash"]
    assert amend_event["actor"] == "human"


def test_amend_drops_obsolete_work(client: TestClient) -> None:
    run_id = _completed_run(client, "Add click analytics and caching")["id"]
    amended = client.post(
        f"/sdlc/runs/{run_id}/amend",
        json={"requirement": "Add click analytics"},
    )
    body = amended.json()
    assert "implement_caching" not in body["nodes"]
    assert "implement_analytics" in body["nodes"]
    artifacts = Path(client.app.state.settings.runs_dir) / run_id / "artifacts"
    assert not (artifacts / "implementation_caching.json").exists()
    trace = client.get(f"/sdlc/runs/{run_id}/trace").json()
    amend_event = next(e for e in trace if "amended" in e["message"])
    assert "implement_caching" in amend_event["extra"]["removed"]


def test_amend_rejects_identical_requirement(client: TestClient) -> None:
    run_id = _completed_run(client, "Add click analytics")["id"]
    response = client.post(
        f"/sdlc/runs/{run_id}/amend", json={"requirement": "Add click analytics"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "requirement_unchanged"


def test_input_hash_is_recorded_and_changes_with_input() -> None:
    from pathlib import Path

    from app.orchestrator.invalidation import compute_input_hash
    from app.orchestrator.models import NodeState, RunState, ScenarioType, utcnow
    from app.orchestrator.planner import plan

    specs = plan("greenfield", "Add caching")
    run = RunState(
        id="99999999-8888-7777-6666-555555555555",
        scenario=ScenarioType.GREENFIELD,
        requirement="Add caching",
        nodes={spec.id: NodeState(spec=spec) for spec in specs},
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    empty = Path("/tmp")
    first = compute_input_hash(run, run.nodes["understand"], empty)
    run.requirement = "Add caching and analytics"
    second = compute_input_hash(run, run.nodes["understand"], empty)
    assert first != second


def test_invalidation_is_surgical(tmp_path: Path) -> None:
    """Sibling implement nodes stay succeeded; join stages reset."""

    from app.orchestrator.invalidation import apply_amend_invalidation, descendants
    from app.orchestrator.models import NodeState, NodeStatus, RunState, ScenarioType, utcnow
    from app.orchestrator.planner import plan

    specs = plan("greenfield", "Add click analytics and caching")
    run = RunState(
        id="12121212-3434-5656-7878-909090909090",
        scenario=ScenarioType.GREENFIELD,
        requirement="Add click analytics and caching",
        nodes={spec.id: NodeState(spec=spec) for spec in specs},
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    for node in run.nodes.values():
        node.status = NodeStatus.SUCCEEDED
        node.input_hash = "kept"

    down = descendants(run, {"implement_caching"})
    assert "test" in down
    assert "document" in down
    assert "understand" not in down
    assert "implement_analytics" not in down

    reset = apply_amend_invalidation(run, ["implement_caching"], {}, str(tmp_path))
    assert "document" in reset
    assert run.nodes["document"].status == NodeStatus.PENDING
    assert run.nodes["implement_analytics"].status == NodeStatus.SUCCEEDED
    assert run.nodes["implement_caching"].status == NodeStatus.PENDING
