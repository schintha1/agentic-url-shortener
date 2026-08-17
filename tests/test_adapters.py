from pathlib import Path

from app.orchestrator.adapters.auth import apply_auth
from app.orchestrator.adapters.caching import apply_caching
from app.orchestrator.adapters.export import apply_export
from app.orchestrator.context import REPO_ROOT
from app.orchestrator.workspace import seed_workspace


def test_export_adapter_adds_csv_route(tmp_path: Path) -> None:
    workspace = seed_workspace(str(tmp_path), "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", REPO_ROOT)
    changed = apply_export(workspace)
    assert "app/shortener/routes.py" in changed
    routes = (workspace / "app/shortener/routes.py").read_text(encoding="utf-8")
    assert "/v1/urls/{code}/export" in routes
    test_src = (workspace / "tests/test_export.py").read_text(encoding="utf-8")
    assert "text/csv" in test_src
    assert "accessed_at,referrer,user_agent" in test_src


def test_caching_adapter_invalidates_on_delete(tmp_path: Path) -> None:
    workspace = seed_workspace(str(tmp_path), "bbbbbbbb-cccc-dddd-eeee-ffffffffffff", REPO_ROOT)
    changed = apply_caching(workspace)
    assert "app/shortener/metadata_cache.py" in changed
    routes = (workspace / "app/shortener/routes.py").read_text(encoding="utf-8")
    assert "metadata_cache.invalidate" in routes
    assert "metadata_cache.get" in routes


def test_auth_adapter_protects_delete(tmp_path: Path) -> None:
    live_routes = (REPO_ROOT / "app/shortener/routes.py").read_text(encoding="utf-8")
    workspace = seed_workspace(str(tmp_path), "cccccccc-dddd-eeee-ffff-111111111111", REPO_ROOT)
    changed = apply_auth(workspace)
    assert "app/shortener/api_key.py" in changed
    assert "app/shortener/routes.py" in changed
    routes = (workspace / "app/shortener/routes.py").read_text(encoding="utf-8")
    assert "require_api_key" in routes
    assert "compare_digest" in (workspace / "app/shortener/api_key.py").read_text(encoding="utf-8")
    test_src = (workspace / "tests/test_domain_auth.py").read_text(encoding="utf-8")
    assert "401" in test_src
    assert "require_api_key" not in live_routes
    assert (REPO_ROOT / "app/shortener/routes.py").read_text(encoding="utf-8") == live_routes
