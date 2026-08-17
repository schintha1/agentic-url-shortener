from fastapi.testclient import TestClient


def test_release_waits_for_approval(client: TestClient) -> None:
    created = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": False},
    )
    body = created.json()
    assert body["status"] == "gate_wait"
    waiting = [node for node in body["nodes"].values() if node["status"] == "gate_wait"]
    assert waiting[0]["spec"]["id"] == "release_approve"
    checklist = client.get(f"/sdlc/runs/{body['id']}/artifacts/release_checklist.md")
    assert checklist.status_code == 200
    assert "Release checklist" in checklist.text
    manifest = client.get(f"/sdlc/runs/{body['id']}/artifacts")
    assert manifest.status_code == 200
    names = {item["name"] for item in manifest.json()}
    assert "release_checklist.md" in names
    approved = client.post(
        f"/sdlc/runs/{body['id']}/approve",
        json={"node_id": "release_approve", "decision": {}, "note": "ship it"},
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
        json={"node_id": "release_approve", "note": "no"},
    )
    assert rejected.json()["status"] == "failed"


def test_audit_chain_records_actor(client: TestClient) -> None:
    created = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": False},
    )
    run_id = created.json()["id"]
    client.post(
        f"/sdlc/runs/{run_id}/approve",
        headers={"X-Approver-Id": "reviewer-7"},
        json={"node_id": "release_approve", "note": "ship it"},
    )
    trace = client.get(f"/sdlc/runs/{run_id}/trace").json()
    assert trace[0]["seq"] == 1
    assert trace[0]["prev_hash"] == ""
    for index in range(1, len(trace)):
        assert trace[index]["seq"] == index + 1
        assert trace[index]["prev_hash"]
    assert any(event["actor"] == "reviewer-7" for event in trace)


def test_reject_writes_an_audit_event(client: TestClient) -> None:
    created = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": False},
    )
    run_id = created.json()["id"]
    client.post(
        f"/sdlc/runs/{run_id}/reject",
        json={"node_id": "release_approve", "note": "no"},
    )
    trace = client.get(f"/sdlc/runs/{run_id}/trace").json()
    assert any(event["to_status"] == "failed" and event["actor"] == "human" for event in trace)


def test_approve_wrong_state_409(client: TestClient) -> None:
    created = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": True},
    )
    response = client.post(
        f"/sdlc/runs/{created.json()['id']}/approve",
        json={"node_id": "release_approve"},
    )
    assert response.status_code == 409
