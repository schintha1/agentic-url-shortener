from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    db_path = tmp_path / "test.db"
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return Settings(
        database_url=f"sqlite:///{db_path}",
        runs_dir=str(runs_dir),
        base_url="http://testserver",
        allow_private_targets=True,
        rate_limit_per_minute=30,
    )


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client
