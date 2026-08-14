"""Run the three SDLC scenarios in-process."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.orchestrator.scenario_pack import ScenarioPack

SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios"


def load_pack(name: str) -> ScenarioPack:
    path = SCENARIO_DIR / f"{name}.json"
    return ScenarioPack.model_validate_json(path.read_text(encoding="utf-8"))


def run_demo(runs_dir: str, database_url: str) -> dict[str, object]:
    settings = Settings(runs_dir=runs_dir, database_url=database_url, allow_private_targets=True)
    results: dict[str, object] = {}
    with TestClient(create_app(settings)) as client:
        for name in ("greenfield", "brownfield"):
            pack = load_pack(name)
            response = client.post(
                "/sdlc/runs",
                json={
                    "scenario": pack.name,
                    "requirement": pack.requirement,
                    "auto_approve": True,
                },
            )
            results[name] = {"status": response.json()["status"], "id": response.json()["id"]}
        pack = load_pack("ambiguous")
        created = client.post(
            "/sdlc/runs",
            json={"scenario": pack.name, "requirement": pack.requirement, "auto_approve": False},
        )
        body = created.json()
        waiting = next(node["spec"]["id"] for node in body["nodes"].values() if node["status"] == "gate_wait")
        approved = client.post(
            f"/sdlc/runs/{body['id']}/approve",
            json={"node_id": waiting, "decision": pack.default_decision, "note": "assume API key"},
        )
        if approved.json()["status"] == "gate_wait":
            release = next(
                node["spec"]["id"]
                for node in approved.json()["nodes"].values()
                if node["status"] == "gate_wait"
            )
            approved = client.post(
                f"/sdlc/runs/{body['id']}/approve",
                json={"node_id": release, "decision": {}, "note": "release"},
            )
        results["ambiguous"] = {
            "status": approved.json()["status"],
            "id": body["id"],
            "nodes": list(approved.json()["nodes"].keys()),
        }
    return results


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        summary = run_demo(tmp, f"sqlite:///{tmp}/demo.db")
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
