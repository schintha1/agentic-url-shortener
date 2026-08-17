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
    dag = json.loads((artifacts / "task_dag.json").read_text(encoding="utf-8"))
    analytics = next(task for task in dag["tasks"] if task["id"] == "implement_analytics")
    assert any(path.startswith("app/shortener/") for path in analytics["files"])
    assert "static_analysis" in dag["parallel_groups"][1]


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
    assert rate["already_present"] is True
    assert rate["changed_files"] == []
    document = (artifacts / "document.md").read_text(encoding="utf-8")
    assert "Validated existing" in document
    assert "no workspace diff declared for this run" in document
    security = json.loads((artifacts / "security_review.json").read_text(encoding="utf-8"))
    finding_ids = {item["id"] for item in security["findings"]}
    assert "new_export_surface" not in finding_ids
    assert "new_auth_surface" not in finding_ids


def test_export_run_produces_a_patch_and_does_not_mutate_the_live_tree(
    client: TestClient,
) -> None:
    live_routes = Path("app/shortener/routes.py").read_text(encoding="utf-8")
    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Add CSV export of click analytics",
            "auto_approve": True,
        },
    )
    body = response.json()
    assert body["status"] == "succeeded"
    artifacts = _artifacts(client, body["id"])
    report = json.loads((artifacts / "implementation_export.json").read_text(encoding="utf-8"))
    assert report["already_present"] is False
    assert report["changed_files"]
    document = (artifacts / "document.md").read_text(encoding="utf-8")
    assert "Capabilities in scope" in document
    assert "Capabilities delivered" not in document
    assert "## Rationale" in document
    assert "## Change" in document
    assert "change.patch" in document
    security = json.loads((artifacts / "security_review.json").read_text(encoding="utf-8"))
    finding_ids = {item["id"] for item in security["findings"]}
    assert "new_export_surface" in finding_ids
    assert "new_auth_surface" not in finding_ids
    assert "export_clicks" in (artifacts / "change.patch").read_text(encoding="utf-8")
    assert Path("app/shortener/routes.py").read_text(encoding="utf-8") == live_routes
    workspace_routes = (
        Path(client.app.state.settings.runs_dir) / body["id"] / "workspace" / "app" / "shortener" / "routes.py"
    )
    assert "def export_clicks" in workspace_routes.read_text(encoding="utf-8")


def test_unknown_capability_fails_closed(client: TestClient, monkeypatch) -> None:
    from app.orchestrator import agents

    monkeypatch.setattr(agents, "ADAPTERS", {})
    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Add CSV export of click analytics",
            "auto_approve": True,
        },
    )
    body = response.json()
    assert body["status"] in {"failed", "rolled_back"}
    assert body["nodes"]["implement_export"]["status"] in {"failed", "rolled_back"}


def test_auth_adapter_run_does_not_mutate_the_live_tree(client: TestClient) -> None:
    live_routes = Path("app/shortener/routes.py").read_text(encoding="utf-8")
    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Add API key authentication",
            "auto_approve": True,
        },
    )
    body = response.json()
    assert body["status"] == "gate_wait"
    artifacts = _artifacts(client, body["id"])
    report = json.loads((artifacts / "implementation_auth.json").read_text(encoding="utf-8"))
    assert report["already_present"] is False
    assert report["changed_files"]
    patch = (artifacts / "change.patch").read_text(encoding="utf-8")
    assert "compare_digest" in patch
    security = json.loads((artifacts / "security_review.json").read_text(encoding="utf-8"))
    assert "new_auth_surface" in {item["id"] for item in security["findings"]}
    assert Path("app/shortener/routes.py").read_text(encoding="utf-8") == live_routes
    workspace_routes = (
        Path(client.app.state.settings.runs_dir)
        / body["id"]
        / "workspace"
        / "app"
        / "shortener"
        / "routes.py"
    )
    assert "require_api_key" in workspace_routes.read_text(encoding="utf-8")
