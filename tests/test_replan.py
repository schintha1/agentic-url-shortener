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

    # The run re-converged after invalidation rather than staying broken.
    assert body["status"] == "succeeded"

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
    first = compute_input_hash(run, run.nodes["design"], empty)
    run.requirement = "Add caching and analytics"
    second = compute_input_hash(run, run.nodes["design"], empty)
    assert first != second


def test_invalidation_is_surgical() -> None:
    """Only the changed node and its descendants reset; siblings are left alone."""

    from pathlib import Path

    from app.orchestrator.invalidation import descendants, invalidate_stale
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
        node.input_hash = "stale-hash-that-will-not-match"

    down = descendants(run, {"implement_caching"})
    assert "test" in down
    assert "document" in down
    assert "understand" not in down

    reset = invalidate_stale(run, Path("/tmp"))
    assert "document" in reset
    assert run.nodes["document"].status == NodeStatus.PENDING
