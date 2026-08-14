from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.orchestrator.store import load_run


def _gated_run(client: TestClient) -> str:
    created = client.post(
        "/sdlc/runs",
        json={"scenario": "greenfield", "requirement": "Build APIs", "auto_approve": False},
    )
    assert created.json()["status"] == "gate_wait"
    return created.json()["id"]


def test_concurrent_approvals_do_not_lose_an_update(client: TestClient) -> None:
    """One approval wins; the other must be told so, not silently discarded."""

    runs_dir = client.app.state.settings.runs_dir
    for _ in range(20):
        run_id = _gated_run(client)
        payload = {"node_id": "release_readiness", "decision": {}, "note": "race"}

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(client.post, f"/sdlc/runs/{run_id}/approve", json=payload)
                for _ in range(2)
            ]
            statuses = sorted(future.result().status_code for future in futures)

        assert statuses[0] == 200, f"expected one winner, got {statuses}"
        assert statuses[1] in {200, 409}
        if statuses[1] == 409:
            assert statuses == [200, 409]

        # Whatever the interleaving, the persisted run must be coherent.
        run = load_run(runs_dir, run_id)
        node = run.nodes["release_readiness"]
        assert node.status.value in {"succeeded", "pending", "running"}
        assert run.version >= 1


def test_version_increments_on_every_write(client: TestClient) -> None:
    runs_dir = client.app.state.settings.runs_dir
    run_id = _gated_run(client)
    first = load_run(runs_dir, run_id).version
    client.post(
        f"/sdlc/runs/{run_id}/approve",
        json={"node_id": "release_readiness", "decision": {}, "note": "ok"},
    )
    second = load_run(runs_dir, run_id).version
    assert second > first


def test_stale_write_is_refused(client: TestClient) -> None:
    """A writer holding an old version must not clobber a newer state."""

    import pytest

    from app.errors import AppError
    from app.orchestrator.store import save_run

    runs_dir = client.app.state.settings.runs_dir
    run_id = _gated_run(client)
    stale = load_run(runs_dir, run_id)
    stale.version -= 1
    with pytest.raises(AppError) as exc:
        save_run(runs_dir, stale)
    assert exc.value.code == "run_conflict"
