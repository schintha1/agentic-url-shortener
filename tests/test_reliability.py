import json
from pathlib import Path

from fastapi.testclient import TestClient

FAILING_TARGET = "tests/fixtures/failing_suite.py"


def _artifacts(client: TestClient, run_id: str) -> Path:
    return Path(client.app.state.settings.runs_dir) / run_id / "artifacts"


def test_retry_recovers(client: TestClient) -> None:
    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Build APIs",
            "auto_approve": True,
            "inject_failure_node": "test",
            "inject_failure_count": 1,
        },
    )
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["retry_count"] >= 1
    assert body["nodes"]["test"]["attempts"] == 2
    assert body["recovered_at"] is not None


def test_implement_rollback(client: TestClient) -> None:
    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Build APIs",
            "auto_approve": True,
            "inject_failure_node": "implement",
            "inject_failure_count": 5,
        },
    )
    body = response.json()
    assert body["status"] == "rolled_back"
    assert body["rollback_count"] >= 1
    assert body["nodes"]["implement"]["status"] == "rolled_back"


def test_stop_leaves_stopped(client: TestClient) -> None:
    created = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": False},
    )
    run_id = created.json()["id"]
    stopped = client.post(f"/sdlc/runs/{run_id}/stop")
    assert stopped.json()["status"] == "stopped"
    assert any(node["status"] == "stopped" for node in stopped.json()["nodes"].values())


def test_failing_domain_suite_fails_the_run(client: TestClient) -> None:
    """The headline reliability claim: a red domain suite must fail the run."""

    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Build APIs",
            "auto_approve": True,
            "domain_test_target": FAILING_TARGET,
        },
    )
    body = response.json()
    assert body["status"] == "failed"
    test_node = body["nodes"]["test"]
    assert test_node["status"] == "failed"
    assert test_node["attempts"] == 2, "the test stage must exhaust its retries before failing"
    report = json.loads(
        (_artifacts(client, body["id"]) / "test_report.json").read_text(encoding="utf-8")
    )
    assert report["passed"] is False
    assert report["target"] == FAILING_TARGET
    assert body["nodes"]["document"]["status"] == "pending"


def test_green_domain_suite_passes_the_run(client: TestClient) -> None:
    """Counterpart to the above: the gate discriminates rather than always failing."""

    response = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": True},
    )
    body = response.json()
    assert body["status"] == "succeeded"
    report = json.loads(
        (_artifacts(client, body["id"]) / "test_report.json").read_text(encoding="utf-8")
    )
    assert report["passed"] is True


def test_optional_stage_failure_degrades_instead_of_blocking(client: TestClient) -> None:
    """Fallback: an optional gate that exhausts retries must not fail the run."""

    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Build APIs",
            "auto_approve": True,
            "inject_failure_node": "static_analysis",
            "inject_failure_count": 5,
        },
    )
    body = response.json()
    assert body["status"] == "succeeded"
    node = body["nodes"]["static_analysis"]
    assert node["status"] == "failed"
    assert node["fallback_applied"] is True
    assert body["fallback_count"] >= 1
    assert body["nodes"]["document"]["status"] == "succeeded"

    trace = client.get(f"/sdlc/runs/{body['id']}/trace").json()
    assert any("fallback_applied" in event["message"] for event in trace)


def test_required_stage_failure_still_blocks(client: TestClient) -> None:
    """The fallback path must be scoped to optional nodes only."""

    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Build APIs",
            "auto_approve": True,
            "inject_failure_node": "security_review",
            "inject_failure_count": 5,
        },
    )
    body = response.json()
    assert body["status"] == "failed"
    assert body["nodes"]["security_review"]["fallback_applied"] is False


def test_resume_recovers_an_interrupted_run(client: TestClient) -> None:
    created = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": False},
    )
    run_id = created.json()["id"]
    run_path = Path(client.app.state.settings.runs_dir) / run_id / "run.json"
    document = json.loads(run_path.read_text(encoding="utf-8"))
    document["status"] = "running"
    document["nodes"]["release_readiness"]["status"] = "running"
    run_path.write_text(json.dumps(document), encoding="utf-8")

    resumed = client.post(f"/sdlc/runs/{run_id}/resume")
    assert resumed.status_code == 200
    body = resumed.json()
    assert body["status"] == "gate_wait"
    assert body["nodes"]["release_readiness"]["status"] == "gate_wait"
    trace = client.get(f"/sdlc/runs/{run_id}/trace").json()
    assert any("resume reset" in event["message"] for event in trace)


def test_resume_rejects_terminal_run(client: TestClient) -> None:
    created = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": True},
    )
    run_id = created.json()["id"]
    response = client.post(f"/sdlc/runs/{run_id}/resume")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_terminal"
