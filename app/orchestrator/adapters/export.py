"""Add GET /v1/urls/{code}/export to a workspace copy of the shortener."""

from pathlib import Path

MARKER = "def export_clicks_csv"
ROUTE_MARKER = 'def export_clicks('

SERVICE_FN = '''

def export_clicks_csv(session: Session, code: str) -> tuple[str, str]:
    """Return (csv_body, filename) for the click history of a short URL."""

    get_url(session, code)
    rows = session.execute(
        select(Click).where(Click.url_code == code).order_by(Click.accessed_at)
    ).scalars().all()
    lines = ["accessed_at,referrer,user_agent"]
    for row in rows:
        referrer = (row.referrer or "").replace('"', '""')
        agent = (row.user_agent or "").replace('"', '""')
        lines.append(f'{row.accessed_at.isoformat()},"{referrer}","{agent}"')
    return "\\n".join(lines) + "\\n", f"{code}-clicks.csv"
'''

ROUTE_FN = '''
@router.get("/v1/urls/{code}/export", summary="Export clicks as CSV")
def export_clicks(code: str, session: SessionDep) -> Response:
    body, filename = service.export_clicks_csv(session, code)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

'''

TEST_SRC = '''from fastapi.testclient import TestClient


def test_export_clicks_csv(client: TestClient) -> None:
    created = client.post("/v1/shorten", json={"url": "https://example.com/export"})
    code = created.json()["code"]
    client.get(
        f"/{code}",
        follow_redirects=False,
        headers={"Referer": "https://ref.example", "User-Agent": "pytest"},
    )
    exported = client.get(f"/v1/urls/{code}/export")
    assert exported.status_code == 200
    assert "text/csv" in exported.headers["content-type"]
    assert exported.text.startswith("accessed_at,referrer,user_agent")
    assert "pytest" in exported.text
'''


def apply_export(workspace: Path) -> list[str]:
    changed: list[str] = []
    service = workspace / "app" / "shortener" / "service.py"
    routes = workspace / "app" / "shortener" / "routes.py"
    test_path = workspace / "tests" / "test_export.py"
    if MARKER not in service.read_text(encoding="utf-8"):
        service.write_text(service.read_text(encoding="utf-8") + SERVICE_FN, encoding="utf-8")
        changed.append("app/shortener/service.py")
    if ROUTE_MARKER not in routes.read_text(encoding="utf-8"):
        text = routes.read_text(encoding="utf-8")
        needle = '@router.get("/{code}", summary="Redirect a short code")'
        if needle not in text:
            raise RuntimeError("export adapter could not find the redirect route to insert before")
        routes.write_text(text.replace(needle, ROUTE_FN + needle, 1), encoding="utf-8")
        changed.append("app/shortener/routes.py")
    if not test_path.exists():
        test_path.write_text(TEST_SRC, encoding="utf-8")
        changed.append("tests/test_export.py")
    return changed
