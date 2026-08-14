from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_ok(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_fails_when_database_missing(tmp_path) -> None:
    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        database_url="sqlite:////no/such/dir/missing.db",
        runs_dir=str(tmp_path / "runs"),
    )
    application = create_app(settings)
    with TestClient(application) as test_client:
        response = test_client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "not_ready"
