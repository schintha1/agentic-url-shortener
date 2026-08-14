from pathlib import Path

import pytest

from app.errors import AppError
from app.orchestrator.policy import check_artifacts


def test_policy_allows_clean_artifact(tmp_path: Path) -> None:
    (tmp_path / "ok.json").write_text('{"stage": "design"}', encoding="utf-8")
    check_artifacts(tmp_path)


def test_policy_rejects_secret(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text('{"token": "sk-abcdefghijklmnop"}', encoding="utf-8")
    with pytest.raises(AppError) as exc:
        check_artifacts(tmp_path)
    assert exc.value.code == "policy_violation"
    assert "sk-" not in exc.value.message


def test_policy_skips_binary(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00")
    check_artifacts(tmp_path)
