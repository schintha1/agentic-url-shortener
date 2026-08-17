"""Add API-key protection to DELETE /v1/urls/{code} in a workspace copy."""

from pathlib import Path

HELPER_MARKER = "def require_api_key"
ROUTE_IMPORT = "from app.shortener.api_key import require_api_key\n"
CONFIG_MARKER = "shortener_api_key"
WORKSPACE_KEY = "workspace-demo-key"

HELPER_SRC = '''"""API-key guard for high-impact shortener routes."""

import secrets
from typing import Annotated

from fastapi import Header, Request

from app.errors import AppError

API_KEY_HEADER = "X-API-Key"


def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    configured = getattr(request.app.state.settings, "shortener_api_key", "") or ""
    if not configured or not x_api_key or not secrets.compare_digest(x_api_key, configured):
        raise AppError(401, "unauthorized", "A valid X-API-Key header is required")
'''

DELETE_NEEDLE = """@router.delete("/v1/urls/{code}", status_code=204, summary="Delete a short URL and its clicks")
def delete_url(code: str, session: SessionDep) -> Response:
    service.delete_url(session, code)
    return Response(status_code=204)
"""

DELETE_REPLACEMENT = """@router.delete("/v1/urls/{code}", status_code=204, summary="Delete a short URL and its clicks")
def delete_url(
    code: str,
    session: SessionDep,
    _: None = Depends(require_api_key),
) -> Response:
    service.delete_url(session, code)
    return Response(status_code=204)
"""

CONFIG_NEEDLE = '    sdlc_api_key: str = ""\n'
CONFIG_REPLACEMENT = (
    '    sdlc_api_key: str = ""\n'
    f'    shortener_api_key: str = "{WORKSPACE_KEY}"\n'
)

TEST_SRC = '''from fastapi.testclient import TestClient


def test_shorten_still_works_without_a_key(client: TestClient) -> None:
    created = client.post("/v1/shorten", json={"url": "https://example.com/open"})
    assert created.status_code == 200
    assert "code" in created.json()


def test_delete_requires_api_key(client: TestClient) -> None:
    created = client.post("/v1/shorten", json={"url": "https://example.com/auth"})
    code = created.json()["code"]
    denied = client.delete(f"/v1/urls/{code}")
    assert denied.status_code == 401
    allowed = client.delete(
        f"/v1/urls/{code}",
        headers={"X-API-Key": "workspace-demo-key"},
    )
    assert allowed.status_code == 204
'''

DELETE_EXISTING = 'client.delete(f"/v1/urls/{code}")'
DELETE_EXISTING_AUTH = f'client.delete(f"/v1/urls/{{code}}", headers={{"X-API-Key": "{WORKSPACE_KEY}"}})'
MISSING_DELETE = 'client.delete("/v1/urls/missing")'
MISSING_DELETE_AUTH = f'client.delete("/v1/urls/missing", headers={{"X-API-Key": "{WORKSPACE_KEY}"}})'


def apply_auth(workspace: Path) -> list[str]:
    changed: list[str] = []
    helper = workspace / "app" / "shortener" / "api_key.py"
    routes = workspace / "app" / "shortener" / "routes.py"
    config = workspace / "app" / "config.py"
    domain_test = workspace / "tests" / "test_domain_auth.py"
    shortener_test = workspace / "tests" / "test_shortener.py"

    if not helper.exists() or HELPER_MARKER not in helper.read_text(encoding="utf-8"):
        helper.write_text(HELPER_SRC, encoding="utf-8")
        changed.append("app/shortener/api_key.py")

    config_text = config.read_text(encoding="utf-8")
    if CONFIG_MARKER not in config_text:
        if CONFIG_NEEDLE not in config_text:
            raise RuntimeError("auth adapter could not find Settings.sdlc_api_key to extend")
        config.write_text(config_text.replace(CONFIG_NEEDLE, CONFIG_REPLACEMENT, 1), encoding="utf-8")
        changed.append("app/config.py")

    text = routes.read_text(encoding="utf-8")
    if ROUTE_IMPORT not in text:
        text = text.replace(
            "from app.shortener import service\n",
            "from app.shortener import service\n" + ROUTE_IMPORT,
            1,
        )
        if DELETE_NEEDLE not in text:
            raise RuntimeError("auth adapter could not find delete_url to protect")
        text = text.replace(DELETE_NEEDLE, DELETE_REPLACEMENT, 1)
        routes.write_text(text, encoding="utf-8")
        changed.append("app/shortener/routes.py")

    if not domain_test.exists():
        domain_test.write_text(TEST_SRC, encoding="utf-8")
        changed.append("tests/test_domain_auth.py")

    if shortener_test.exists():
        tests = shortener_test.read_text(encoding="utf-8")
        patched = tests.replace(DELETE_EXISTING, DELETE_EXISTING_AUTH).replace(
            MISSING_DELETE, MISSING_DELETE_AUTH
        )
        if patched != tests:
            shortener_test.write_text(patched, encoding="utf-8")
            changed.append("tests/test_shortener.py")

    return changed
