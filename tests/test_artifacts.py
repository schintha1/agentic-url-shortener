from pathlib import Path

import pytest

from app.errors import AppError
from app.orchestrator.artifacts import (
    TestReport,
    schema_for,
    validate_artifact,
    validate_declared,
)


def test_schema_lookup_for_per_capability_artifacts() -> None:
    assert schema_for("implementation_rate_limit.json") is not None
    assert schema_for("implementation.json") is not None
    assert schema_for("unknown_artifact.json") is None


def test_missing_artifact_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AppError) as exc:
        validate_artifact(tmp_path, "test_report.json")
    assert exc.value.code == "artifact_missing"


def test_empty_artifact_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "test_report.json").write_text("", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        validate_artifact(tmp_path, "test_report.json")
    assert exc.value.code == "artifact_missing"


def test_malformed_json_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "test_report.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        validate_artifact(tmp_path, "test_report.json")
    assert exc.value.code == "artifact_invalid"


def test_schema_violation_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "test_report.json").write_text('{"exit_code": "not-an-int"}', encoding="utf-8")
    with pytest.raises(AppError) as exc:
        validate_artifact(tmp_path, "test_report.json")
    assert exc.value.code == "artifact_invalid"


def test_valid_artifact_passes(tmp_path: Path) -> None:
    payload = TestReport(exit_code=0, passed=True, target="tests/x.py", output="ok")
    (tmp_path / "test_report.json").write_text(payload.model_dump_json(), encoding="utf-8")
    validate_declared(tmp_path, ["test_report.json"])


def test_markdown_artifact_only_needs_content(tmp_path: Path) -> None:
    (tmp_path / "design.md").write_text("# Design\n", encoding="utf-8")
    validate_declared(tmp_path, ["design.md"])
    (tmp_path / "document.md").write_text("", encoding="utf-8")
    with pytest.raises(AppError):
        validate_declared(tmp_path, ["document.md"])
