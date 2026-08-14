from fastapi.testclient import TestClient


def test_linear_stub_run_completes(client: TestClient) -> None:
    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Build a URL shortener with core APIs",
            "auto_approve": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert all(node["status"] == "succeeded" for node in body["nodes"].values())
    fetched = client.get(f"/sdlc/runs/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


def test_unknown_run_404(client: TestClient) -> None:
    response = client.get("/sdlc/runs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


def test_requirement_too_long(client: TestClient) -> None:
    response = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "x" * 8001, "auto_approve": True},
    )
    assert response.status_code == 422
