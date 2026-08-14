from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.shortener.models import Url


def test_shorten_and_redirect(client: TestClient) -> None:
    created = client.post("/v1/shorten", json={"url": "https://example.com/path"})
    assert created.status_code == 200
    body = created.json()
    assert body["original_url"] == "https://example.com/path"
    code = body["code"]
    redirect = client.get(f"/{code}", follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"] == "https://example.com/path"


def test_custom_alias(client: TestClient) -> None:
    created = client.post(
        "/v1/shorten",
        json={"url": "https://example.com/alias", "custom_alias": "my-link"},
    )
    assert created.status_code == 200
    assert created.json()["code"] == "my-link"
    meta = client.get("/v1/urls/my-link")
    assert meta.status_code == 200
    assert meta.json()["original_url"] == "https://example.com/alias"


def test_custom_alias_conflict(client: TestClient) -> None:
    payload = {"url": "https://example.com/a", "custom_alias": "taken1"}
    assert client.post("/v1/shorten", json=payload).status_code == 200
    conflict = client.post(
        "/v1/shorten",
        json={"url": "https://example.com/b", "custom_alias": "taken1"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "alias_conflict"


def test_javascript_url_rejected(client: TestClient) -> None:
    response = client.post("/v1/shorten", json={"url": "javascript:alert(1)"})
    assert response.status_code == 422


def test_unknown_code_404(client: TestClient) -> None:
    response = client.get("/v1/urls/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_expired_url_410(client: TestClient) -> None:
    factory = client.app.state.session_factory
    session = factory()
    try:
        session.add(
            Url(
                code="expired",
                original_url="https://example.com/old",
                created_at=datetime.now(UTC) - timedelta(hours=2),
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        session.commit()
    finally:
        session.close()
    response = client.get("/expired", follow_redirects=False)
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "expired"


def test_stats_after_redirect(client: TestClient) -> None:
    created = client.post("/v1/shorten", json={"url": "https://example.com/stats"})
    code = created.json()["code"]
    client.get(
        f"/{code}",
        follow_redirects=False,
        headers={"Referer": "https://news.example.com", "User-Agent": "pytest"},
    )
    stats = client.get(f"/v1/urls/{code}/stats")
    assert stats.status_code == 200
    body = stats.json()
    assert body["clicks"] == 1
    assert body["last_access"] is not None
    assert body["top_referrers"][0]["value"] == "https://news.example.com"
    assert body["top_user_agents"][0]["value"] == "pytest"


def test_stats_unknown_code(client: TestClient) -> None:
    response = client.get("/v1/urls/missing/stats")
    assert response.status_code == 404


def test_idempotency_replay(client: TestClient) -> None:
    headers = {"Idempotency-Key": "abc-123"}
    payload = {"url": "https://example.com/idem"}
    first = client.post("/v1/shorten", json=payload, headers=headers)
    second = client.post("/v1/shorten", json=payload, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["code"] == second.json()["code"]


def test_idempotency_conflict(client: TestClient) -> None:
    headers = {"Idempotency-Key": "same-key"}
    client.post("/v1/shorten", json={"url": "https://example.com/one"}, headers=headers)
    conflict = client.post(
        "/v1/shorten", json={"url": "https://example.com/two"}, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_rate_limit(tmp_path) -> None:
    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'rl.db'}",
        runs_dir=str(tmp_path / "runs"),
        rate_limit_per_minute=2,
        allow_private_targets=True,
    )
    with TestClient(create_app(settings)) as limited:
        assert limited.post("/v1/shorten", json={"url": "https://example.com/a"}).status_code == 200
        assert limited.post("/v1/shorten", json={"url": "https://example.com/b"}).status_code == 200
        blocked = limited.post("/v1/shorten", json={"url": "https://example.com/c"})
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "rate_limited"
        assert "Retry-After" in blocked.headers
