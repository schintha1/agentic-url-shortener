from fastapi.testclient import TestClient


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
