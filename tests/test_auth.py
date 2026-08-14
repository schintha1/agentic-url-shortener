from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

API_KEY = "test-control-plane-key"


@pytest.fixture()
def secured_client(settings: Settings) -> Iterator[TestClient]:
    secured = settings.model_copy(update={"sdlc_api_key": API_KEY})
    with TestClient(create_app(secured)) as client:
        yield client


def _run_payload() -> dict[str, object]:
    return {"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": True}


def test_missing_key_is_rejected(secured_client: TestClient) -> None:
    response = secured_client.post("/sdlc/runs", json=_run_payload())
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthorized"
    assert "message" in body["error"]


def test_wrong_key_is_rejected(secured_client: TestClient) -> None:
    response = secured_client.post(
        "/sdlc/runs", json=_run_payload(), headers={"X-API-Key": "not-the-key"}
    )
    assert response.status_code == 401


def test_correct_key_is_accepted(secured_client: TestClient) -> None:
    response = secured_client.post(
        "/sdlc/runs", json=_run_payload(), headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 200


def test_read_endpoints_are_also_guarded(secured_client: TestClient) -> None:
    assert secured_client.get("/sdlc/metrics").status_code == 401
    assert (
        secured_client.get("/sdlc/metrics", headers={"X-API-Key": API_KEY}).status_code == 200
    )


def test_shortener_stays_open_when_control_plane_is_locked(secured_client: TestClient) -> None:
    """Auth belongs on the control plane, not on the public redirect service."""

    assert secured_client.get("/health").status_code == 200
    created = secured_client.post("/v1/shorten", json={"url": "https://example.com/open"})
    assert created.status_code == 200


def test_control_plane_is_open_when_unconfigured(client: TestClient) -> None:
    """The local demo path must keep working without a key."""

    assert client.get("/sdlc/metrics").status_code == 200
