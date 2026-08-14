from fastapi.testclient import TestClient


def test_release_waits_for_approval(client: TestClient) -> None:
    created = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": False},
    )
    body = created.json()
    assert body["status"] == "gate_wait"
    waiting = [node for node in body["nodes"].values() if node["status"] == "gate_wait"]
    assert waiting[0]["spec"]["id"] == "release_readiness"
    approved = client.post(
        f"/sdlc/runs/{body['id']}/approve",
        json={"node_id": "release_readiness", "decision": {}, "note": "ship it"},
    )
    assert approved.json()["status"] == "succeeded"


def test_reject_fails_run(client: TestClient) -> None:
    created = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": False},
    )
    run_id = created.json()["id"]
    rejected = client.post(
        f"/sdlc/runs/{run_id}/reject",
        json={"node_id": "release_readiness", "note": "no"},
    )
    assert rejected.json()["status"] == "failed"


def test_approve_wrong_state_409(client: TestClient) -> None:
    created = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": True},
    )
    response = client.post(
        f"/sdlc/runs/{created.json()['id']}/approve",
        json={"node_id": "release_readiness"},
    )
    assert response.status_code == 409
