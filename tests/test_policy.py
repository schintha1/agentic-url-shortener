from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from app.orchestrator.policy import (
    PolicyPack,
    check_artifacts,
    check_release_compliance,
    requires_change_control,
)


def test_every_pack_has_at_least_one_rule() -> None:
    """A registered pack with no enforcement is decoration."""

    from app.orchestrator.policy import COMPLIANCE_EVIDENCE, CONTENT_RULES

    security_rules = [rule for rule in CONTENT_RULES if rule.pack is PolicyPack.SECURITY]
    assert security_rules
    assert COMPLIANCE_EVIDENCE
    assert requires_change_control.__doc__


def test_policy_allows_clean_artifact(tmp_path: Path) -> None:
    (tmp_path / "ok.json").write_text('{"stage": "design"}', encoding="utf-8")
    check_artifacts(tmp_path)


def test_policy_rejects_secret(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text('{"token": "sk-abcdefghijklmnop"}', encoding="utf-8")
    with pytest.raises(AppError) as exc:
        check_artifacts(tmp_path)
    assert exc.value.code == "policy_violation"
    assert "sk-" not in exc.value.message
    assert "secret_token" in exc.value.message


def test_policy_rejects_private_key(tmp_path: Path) -> None:
    (tmp_path / "key.md").write_text("-----BEGIN RSA PRIVATE KEY-----", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        check_artifacts(tmp_path)
    assert "private_key_block" in exc.value.message


def test_policy_rejects_pii(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("contact alice@example.com", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        check_artifacts(tmp_path)
    assert "pii_email" in exc.value.message
    assert "alice@example.com" not in exc.value.message


def test_policy_skips_binary(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00")
    check_artifacts(tmp_path)


def test_policy_scopes_to_named_files(tmp_path: Path) -> None:
    (tmp_path / "clean.json").write_text("{}", encoding="utf-8")
    (tmp_path / "dirty.json").write_text('{"t": "sk-abcdefghijklmnop"}', encoding="utf-8")
    check_artifacts(tmp_path, only=["clean.json"])
    with pytest.raises(AppError):
        check_artifacts(tmp_path, only=["dirty.json"])


def test_compliance_reports_missing_evidence(tmp_path: Path) -> None:
    assert set(check_release_compliance(tmp_path)) == {
        "security_review.json",
        "test_report.json",
    }
    (tmp_path / "security_review.json").write_text('{"findings": []}', encoding="utf-8")
    assert check_release_compliance(tmp_path) == ["test_report.json"]


def test_compliance_satisfied_when_evidence_present(tmp_path: Path) -> None:
    (tmp_path / "security_review.json").write_text('{"findings": []}', encoding="utf-8")
    (tmp_path / "test_report.json").write_text('{"passed": true}', encoding="utf-8")
    assert check_release_compliance(tmp_path) == []


def test_change_control_flags_high_impact_capability(tmp_path: Path) -> None:
    (tmp_path / "implementation_auth.json").write_text(
        '{"capability": "auth", "target_endpoints": []}', encoding="utf-8"
    )
    needed, reason = requires_change_control(tmp_path)
    assert needed is True
    assert "auth" in reason


def test_change_control_flags_destructive_endpoint(tmp_path: Path) -> None:
    (tmp_path / "implementation_analytics.json").write_text(
        '{"capability": "analytics", "target_endpoints": ["DELETE /v1/urls/{code}"]}',
        encoding="utf-8",
    )
    needed, reason = requires_change_control(tmp_path)
    assert needed is True
    assert "DELETE" in reason


def test_change_control_ignores_low_impact_change(tmp_path: Path) -> None:
    (tmp_path / "implementation_analytics.json").write_text(
        '{"capability": "analytics", "target_endpoints": ["GET /v1/urls/{code}/stats"]}',
        encoding="utf-8",
    )
    needed, _ = requires_change_control(tmp_path)
    assert needed is False


def test_auto_approve_cannot_release_a_high_impact_change(client: TestClient) -> None:
    """The strongest governance assertion: an agent may not self-grant release authority."""

    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Add API key authentication",
            "auto_approve": True,
        },
    )
    body = response.json()
    assert body["status"] == "gate_wait", "change control must override auto_approve"
    release = body["nodes"]["release_readiness"]
    assert release["status"] == "gate_wait"
    assert release["change_controlled"] is True

    trace = client.get(f"/sdlc/runs/{body['id']}/trace").json()
    assert any("change control withdrew auto-approval" in event["message"] for event in trace)

    approved = client.post(
        f"/sdlc/runs/{body['id']}/approve",
        json={"node_id": "release_readiness", "decision": {}, "note": "reviewed by a human"},
    )
    assert approved.json()["status"] == "succeeded"


def test_auto_approve_still_works_for_low_impact_change(client: TestClient) -> None:
    """Change control must be targeted, not a blanket disabling of autonomy."""

    response = client.post(
        "/sdlc/runs",
        json={
            "scenario": "greenfield",
            "requirement": "Add click analytics",
            "auto_approve": True,
        },
    )
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["nodes"]["release_readiness"]["change_controlled"] is False
