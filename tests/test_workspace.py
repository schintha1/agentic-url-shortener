from pathlib import Path

from app.orchestrator.adapters.caching import apply_caching
from app.orchestrator.adapters.export import apply_export
from app.orchestrator.context import REPO_ROOT
from app.orchestrator.workspace import seed_workspace, workspace_fingerprint, write_unified_diff


def test_seed_workspace_copies_app_and_tests(tmp_path: Path) -> None:
    run_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    dest = seed_workspace(str(tmp_path), run_id, REPO_ROOT)
    assert (dest / "app" / "shortener" / "routes.py").exists()
    assert (dest / "tests" / "test_shortener.py").exists()
    assert (dest / "pyproject.toml").exists()


def test_export_adapter_is_idempotent(tmp_path: Path) -> None:
    run_id = "11111111-2222-3333-4444-555555555555"
    workspace = seed_workspace(str(tmp_path), run_id, REPO_ROOT)
    first = apply_export(workspace)
    second = apply_export(workspace)
    assert first
    assert second == []
    assert "def export_clicks" in (workspace / "app/shortener/routes.py").read_text(encoding="utf-8")


def test_caching_adapter_writes_cache_module(tmp_path: Path) -> None:
    run_id = "99999999-8888-7777-6666-555555555555"
    workspace = seed_workspace(str(tmp_path), run_id, REPO_ROOT)
    changed = apply_caching(workspace)
    assert "app/shortener/metadata_cache.py" in changed
    assert (workspace / "app/shortener/metadata_cache.py").exists()


def test_fingerprint_changes_when_workspace_is_patched(tmp_path: Path) -> None:
    run_id = "12121212-3434-5656-7878-909090909090"
    workspace = seed_workspace(str(tmp_path), run_id, REPO_ROOT)
    before = workspace_fingerprint(workspace)
    apply_export(workspace)
    after = workspace_fingerprint(workspace)
    assert before != after
    write_unified_diff(str(tmp_path), run_id)
    patch = tmp_path / run_id / "artifacts" / "change.patch"
    assert patch.exists()
    assert patch.stat().st_size > 0
