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


def test_empty_runs_dir_reports_zeroes(client: TestClient) -> None:
    body = client.get("/sdlc/metrics?recompute=true").json()
    assert body["runs_total"] == 0
    assert body["success_rate"] == 0.0
    assert body["mttr_ms"] == 0.0


def test_rollup_matches_full_recompute(client: TestClient) -> None:
    """The cache must never drift from the truth it summarises."""

    client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": True},
    )
    client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Build APIs",
            "auto_approve": True,
            "inject_failure_node": "test",
            "inject_failure_count": 1,
        },
    )
    client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Build APIs",
            "auto_approve": True,
            "inject_failure_node": "security_review",
            "inject_failure_count": 5,
        },
    )
    cached = client.get("/sdlc/metrics").json()
    fresh = client.get("/sdlc/metrics?recompute=true").json()
    assert cached == fresh, "rollup drifted from a full scan"
    assert fresh["runs_total"] == 3
    assert fresh["succeeded"] == 2
    assert fresh["failed"] == 1


def test_rates_are_reported_not_just_counts(client: TestClient) -> None:
    client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Build APIs",
            "auto_approve": True,
            "inject_failure_node": "test",
            "inject_failure_count": 1,
        },
    )
    body = client.get("/sdlc/metrics?recompute=true").json()
    assert body["retry_count"] >= 1
    assert body["retry_rate"] == body["retry_count"] / body["runs_total"]
    assert "rollback_rate" in body
    assert "fallback_rate" in body


def test_mttr_is_measured_per_incident(client: TestClient) -> None:
    """A recovered failure yields a positive MTTR derived from two audit events."""

    client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Build APIs",
            "auto_approve": True,
            "inject_failure_node": "test",
            "inject_failure_count": 1,
        },
    )
    body = client.get("/sdlc/metrics?recompute=true").json()
    assert body["incidents"] >= 1
    assert body["mttr_ms"] > 0.0


def test_mttr_is_zero_without_incidents(client: TestClient) -> None:
    client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": True},
    )
    body = client.get("/sdlc/metrics?recompute=true").json()
    assert body["incidents"] == 0
    assert body["mttr_ms"] == 0.0


def test_rollup_survives_a_restart(client: TestClient) -> None:
    from app.orchestrator.metrics import read_rollup

    client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": True},
    )
    persisted = read_rollup(client.app.state.settings.runs_dir)
    assert persisted is not None
    assert persisted.runs_total >= 1
