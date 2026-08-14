from fastapi.testclient import TestClient

from app.demo import load_pack


def test_greenfield_pack(client: TestClient) -> None:
    pack = load_pack("greenfield")
    response = client.post(
        "/sdlc/runs",
        json={"scenario": pack.name, "requirement": pack.requirement, "auto_approve": True},
    )
    assert response.json()["status"] == "succeeded"


def test_brownfield_pack(client: TestClient) -> None:
    pack = load_pack("brownfield")
    response = client.post(
        "/sdlc/runs",
        json={"scenario": pack.name, "requirement": pack.requirement, "auto_approve": True},
    )
    assert response.json()["status"] == "succeeded"
    assert "impact_analysis" in response.json()["nodes"]


def test_ambiguous_replan(client: TestClient) -> None:
    pack = load_pack("ambiguous")
    created = client.post(
        "/sdlc/runs",
        json={"scenario": pack.name, "requirement": pack.requirement, "auto_approve": False},
    )
    body = created.json()
    assert body["status"] == "gate_wait"
    assert body["nodes"]["understand"]["status"] == "gate_wait"
    first = client.post(
        f"/sdlc/runs/{body['id']}/approve",
        json={"node_id": "understand", "decision": pack.default_decision, "note": "api key"},
    )
    assert "apply_assumptions" in first.json()["nodes"]
    if first.json()["status"] == "gate_wait":
        first = client.post(
            f"/sdlc/runs/{body['id']}/approve",
            json={"node_id": "release_readiness", "decision": {}, "note": "ok"},
        )
    assert first.json()["status"] == "succeeded"
    assert first.json()["nodes"]["apply_assumptions"]["status"] == "succeeded"
