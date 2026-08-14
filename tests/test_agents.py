from pathlib import Path

from fastapi.testclient import TestClient


def test_agents_write_typed_artifacts(client: TestClient) -> None:
    response = client.post(
        "/sdlc/runs",
        json={"scenario": "brownfield", "requirement": "Add analytics", "auto_approve": True},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    run_id = response.json()["id"]
    runs_dir = Path(client.app.state.settings.runs_dir)
    artifacts = runs_dir / run_id / "artifacts"
    assert (artifacts / "requirement_brief.json").exists()
    assert (artifacts / "task_dag.json").exists()
    assert (artifacts / "impact.json").exists()
    assert (artifacts / "test_report.json").exists()
    impact = (artifacts / "impact.json").read_text(encoding="utf-8")
    assert "app/shortener/routes.py" in impact
    test_report = (artifacts / "test_report.json").read_text(encoding="utf-8")
    assert '"passed": true' in test_report
