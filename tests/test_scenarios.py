from fastapi.testclient import TestClient

from app.demo import load_pack


def test_greenfield_pack(client: TestClient) -> None:
    pack = load_pack("greenfield")
    response = client.post(
        "/sdlc/runs",
        json={"scenario": pack.name, "requirement": pack.requirement, "auto_approve": True},
    )
    body = response.json()
    assert body["status"] == "succeeded"
    assert "implement_export" in body["nodes"]
    diff = client.get(f"/sdlc/runs/{body['id']}/diff")
    assert diff.status_code == 200
    assert "export_clicks" in diff.text


def test_brownfield_pack(client: TestClient) -> None:
    pack = load_pack("brownfield")
    response = client.post(
        "/sdlc/runs",
        json={"scenario": pack.name, "requirement": pack.requirement, "auto_approve": True},
    )
    body = response.json()
    assert body["status"] == "succeeded"
    assert "impact_analysis" in body["nodes"]
    assert "implement_caching" in body["nodes"]
    diff = client.get(f"/sdlc/runs/{body['id']}/diff")
    assert diff.status_code == 200
    assert "metadata_cache" in diff.text


def test_ambiguous_replan(client: TestClient) -> None:
    pack = load_pack("ambiguous")
    created = client.post(
        "/sdlc/runs",
        json={"scenario": pack.name, "requirement": pack.requirement, "auto_approve": False},
    )
    body = created.json()
    assert body["status"] == "gate_wait"
    assert body["nodes"]["understand"]["status"] == "succeeded"
    assert body["nodes"]["confirm_scope"]["status"] == "gate_wait"
    brief = client.get(f"/sdlc/runs/{body['id']}/artifacts/requirement_brief.json")
    assert brief.status_code == 200
    first = client.post(
        f"/sdlc/runs/{body['id']}/approve",
        json={"node_id": "confirm_scope", "decision": pack.default_decision, "note": "api key"},
    )
    assert "apply_assumptions" in first.json()["nodes"]
    assert "implement_auth" in first.json()["nodes"]
    assert "implement" not in first.json()["nodes"]
    if first.json()["status"] == "gate_wait":
        first = client.post(
            f"/sdlc/runs/{body['id']}/approve",
            json={
                "node_id": "release_approve",
                "decision": {"waiver": "demo"},
                "note": "ok",
            },
        )
    assert first.json()["status"] == "succeeded"
    assert first.json()["nodes"]["apply_assumptions"]["status"] == "succeeded"
    design = client.get(f"/sdlc/runs/{body['id']}/artifacts/design.md")
    assert "api_key" in design.text
    diff = client.get(f"/sdlc/runs/{body['id']}/diff")
    assert diff.status_code == 200
    assert "compare_digest" in diff.text
