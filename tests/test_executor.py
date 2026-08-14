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


def test_policy_fails_run(client: TestClient, monkeypatch) -> None:
    def bad_stage(stage: str, run, runs_dir: str) -> None:
        from app.orchestrator.store import artifacts_dir

        path = artifacts_dir(runs_dir, run.id) / f"{stage}.json"
        path.write_text('{"secret": "sk-abcdefghijklmnop"}', encoding="utf-8")

    monkeypatch.setattr("app.orchestrator.executor.run_stage", bad_stage)
    response = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": True},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"


def test_requirement_shapes_the_executed_graph(client: TestClient) -> None:
    """End-to-end proof that the requirement, not just the scenario, drives the run."""

    first = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Add rate limiting and click analytics",
            "auto_approve": True,
        },
    )
    assert first.status_code == 200
    first_nodes = set(first.json()["nodes"])
    assert "implement_rate_limit" in first_nodes
    assert "implement_analytics" in first_nodes

    second = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Add API key authentication",
            "auto_approve": True,
        },
    )
    second_nodes = set(second.json()["nodes"])
    assert "implement_auth" in second_nodes
    assert "implement_rate_limit" not in second_nodes
    assert first_nodes != second_nodes


def test_parallel_implementations_all_execute(client: TestClient) -> None:
    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Add rate limiting and click analytics",
            "auto_approve": True,
        },
    )
    body = response.json()
    assert body["status"] == "succeeded"
    implement_nodes = {k: v for k, v in body["nodes"].items() if k.startswith("implement_")}
    assert len(implement_nodes) >= 2
    assert all(node["status"] == "succeeded" for node in implement_nodes.values())
    test_start = body["nodes"]["test"]["started_at"]
    for node in implement_nodes.values():
        assert node["finished_at"] <= test_start


def test_parallel_join_order(client: TestClient) -> None:
    response = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": True},
    )
    nodes = response.json()["nodes"]
    test_done = nodes["test"]["finished_at"]
    sec_done = nodes["security_review"]["finished_at"]
    doc_start = nodes["document"]["started_at"]
    assert test_done is not None and sec_done is not None and doc_start is not None
    assert doc_start >= test_done
    assert doc_start >= sec_done
