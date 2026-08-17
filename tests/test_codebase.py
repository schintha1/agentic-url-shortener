from pathlib import Path

from app.orchestrator.codebase import scan
from app.orchestrator.requirements import Capability

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_discovers_real_endpoints() -> None:
    codebase = scan(REPO_ROOT)
    labels = codebase.endpoint_labels()
    assert "POST /v1/shorten" in labels
    assert "GET /{code}" in labels
    assert "GET /v1/urls/{code}/stats" in labels


def test_discovers_real_tables() -> None:
    codebase = scan(REPO_ROOT)
    names = codebase.table_names()
    assert {"urls", "clicks", "idempotency_keys"} <= set(names)
    urls = next(table for table in codebase.tables if table.name == "urls")
    assert "original_url" in urls.columns


def test_impact_maps_capability_to_real_modules() -> None:
    codebase = scan(REPO_ROOT)
    report = codebase.impacted_by([Capability.RATE_LIMIT])
    assert "app/shortener/rate_limit.py" in report.modules
    assert report.scanned_modules > 5
    entry = report.per_capability[0]
    assert entry.capability == Capability.RATE_LIMIT
    assert entry.confidence in {"low", "medium", "high"}


def test_impact_differs_by_capability() -> None:
    codebase = scan(REPO_ROOT)
    rate = set(codebase.impacted_by([Capability.RATE_LIMIT]).modules)
    idem = set(codebase.impacted_by([Capability.IDEMPOTENCY]).modules)
    assert rate != idem


def test_scan_reflects_new_code(tmp_path: Path) -> None:
    """The report must grow when the source tree grows, not when a list is edited."""

    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    routes = package / "routes.py"
    routes.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n\n"
        '@router.get("/first")\n'
        "def first() -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    before = scan(tmp_path).endpoint_labels()
    assert before == ["GET /first"]

    routes.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n\n"
        '@router.get("/first")\n'
        "def first() -> None:\n"
        "    return None\n\n"
        '@router.post("/second")\n'
        "def second() -> None:\n"
        "    return None\n",
        encoding="utf-8",
    )
    after = scan(tmp_path).endpoint_labels()
    assert after == ["GET /first", "POST /second"]


def test_export_is_not_already_present_in_the_live_tree() -> None:
    codebase = scan(REPO_ROOT)
    report = codebase.impacted_by([Capability.EXPORT])
    assert Capability.EXPORT in report.unmatched_capabilities


def test_auth_is_not_already_present_in_the_shortener() -> None:
    codebase = scan(REPO_ROOT)
    report = codebase.impacted_by([Capability.AUTH])
    assert Capability.AUTH in report.unmatched_capabilities
    assert report.modules == []


def test_retention_impact_stays_in_shortener() -> None:
    codebase = scan(REPO_ROOT)
    report = codebase.impacted_by([Capability.RETENTION])
    assert Capability.RETENTION not in report.unmatched_capabilities
    assert report.modules
    assert all(path.startswith("app/shortener/") for path in report.modules)
    assert all("orchestrator" not in path for path in report.modules)


def test_impact_never_names_orchestrator_auth() -> None:
    codebase = scan(REPO_ROOT)
    report = codebase.impacted_by([Capability.AUTH, Capability.RATE_LIMIT])
    assert "app/orchestrator/auth.py" not in report.modules
    assert Capability.AUTH in report.unmatched_capabilities


def test_missing_package_is_empty() -> None:
    codebase = scan("/tmp/definitely-not-a-repo-xyz")
    assert codebase.modules == {}
    assert codebase.endpoint_labels() == []
