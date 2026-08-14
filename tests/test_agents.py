import json
from pathlib import Path

from fastapi.testclient import TestClient


def _artifacts(client: TestClient, run_id: str) -> Path:
    return Path(client.app.state.settings.runs_dir) / run_id / "artifacts"


def test_agents_write_typed_artifacts(client: TestClient) -> None:
    response = client.post(
        "/sdlc/runs",
        json={"scenario": "brownfield", "requirement": "Add analytics", "auto_approve": True},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    artifacts = _artifacts(client, response.json()["id"])
    assert (artifacts / "requirement_brief.json").exists()
    assert (artifacts / "task_dag.json").exists()
    assert (artifacts / "impact.json").exists()
    assert (artifacts / "test_report.json").exists()
    report = json.loads((artifacts / "test_report.json").read_text(encoding="utf-8"))
    assert report["passed"] is True


def test_impact_report_names_real_endpoints(client: TestClient) -> None:
    """Impact analysis must come from the source tree, not a maintained literal."""

    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "brownfield",
            "requirement": "Add rate limiting and click analytics",
            "auto_approve": True,
        },
    )
    artifacts = _artifacts(client, response.json()["id"])
    impact = json.loads((artifacts / "impact.json").read_text(encoding="utf-8"))
    assert "app/shortener/rate_limit.py" in impact["modules"]
    assert impact["scanned_modules"] > 5
    routes = Path("app/shortener/routes.py").read_text(encoding="utf-8")
    for endpoint in impact["apis"]:
        path = endpoint.split(" ", 1)[1]
        assert path in routes, f"{path} is not present in routes.py"
    capabilities = {entry["capability"] for entry in impact["per_capability"]}
    assert "rate_limit" in capabilities


def test_requirement_brief_records_analysis(client: TestClient) -> None:
    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Add caching for redirects",
            "auto_approve": True,
        },
    )
    artifacts = _artifacts(client, response.json()["id"])
    brief = json.loads((artifacts / "requirement_brief.json").read_text(encoding="utf-8"))
    assert "caching" in brief["capabilities"]
    assert brief["intent"] == "Add caching for redirects"


def test_implementation_artifact_is_per_capability(client: TestClient) -> None:
    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Add rate limiting and click analytics",
            "auto_approve": True,
        },
    )
    artifacts = _artifacts(client, response.json()["id"])
    rate = json.loads((artifacts / "implementation_rate_limit.json").read_text(encoding="utf-8"))
    analytics = json.loads(
        (artifacts / "implementation_analytics.json").read_text(encoding="utf-8")
    )
    assert rate["capability"] == "rate_limit"
    assert analytics["capability"] == "analytics"
    assert rate["target_modules"] != analytics["target_modules"]
