from fastapi.testclient import TestClient


def test_metrics_and_trace(client: TestClient) -> None:
    created = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": True},
    )
    run_id = created.json()["id"]
    trace = client.get(f"/sdlc/runs/{run_id}/trace")
    assert trace.status_code == 200
    events = trace.json()
    assert len(events) > 0
    assert events[0]["actor"] in {"agent", "system", "human"}
    metrics = client.get("/sdlc/metrics")
    body = metrics.json()
    assert body["runs_total"] >= 1
    assert 0.0 <= body["success_rate"] <= 1.0
    assert body["e2e_latency_ms_avg"] >= 0.0
