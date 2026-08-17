"""Add an in-process metadata cache to a workspace copy of the shortener."""

from pathlib import Path

CACHE_MODULE = '''"""Process-local metadata cache. Invalidated on delete."""

from __future__ import annotations

from typing import Any


class MetadataCache:
    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self.hits = 0
        self.misses = 0

    def get(self, code: str) -> Any | None:
        if code in self._store:
            self.hits += 1
            return self._store[code]
        self.misses += 1
        return None

    def set(self, code: str, value: Any) -> None:
        self._store[code] = value

    def invalidate(self, code: str) -> None:
        self._store.pop(code, None)


metadata_cache = MetadataCache()
'''

TEST_SRC = '''from app.shortener.metadata_cache import MetadataCache


def test_metadata_cache_roundtrip() -> None:
    cache = MetadataCache()
    cache.set("abc", {"code": "abc"})
    assert cache.get("abc") == {"code": "abc"}
    assert cache.hits == 1
    cache.invalidate("abc")
    assert cache.get("abc") is None
'''

GET_IMPORT = "from app.shortener.metadata_cache import metadata_cache\n"
GET_NEEDLE = """@router.get("/v1/urls/{code}", response_model=UrlMetadata, summary="Get short URL metadata")
def get_metadata(
    code: str,
    session: SessionDep,
) -> UrlMetadata:
    record = service.get_url(session, code)
"""
GET_REPLACEMENT = """@router.get("/v1/urls/{code}", response_model=UrlMetadata, summary="Get short URL metadata")
def get_metadata(
    code: str,
    session: SessionDep,
) -> UrlMetadata:
    cached = metadata_cache.get(code)
    if cached is not None:
        return cached
    record = service.get_url(session, code)
"""
SET_NEEDLE = """    return UrlMetadata(
        code=record.code,
        original_url=record.original_url,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )
"""
SET_REPLACEMENT = """    payload = UrlMetadata(
        code=record.code,
        original_url=record.original_url,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )
    metadata_cache.set(code, payload)
    return payload
"""
DELETE_NEEDLE = """def delete_url(code: str, session: SessionDep) -> Response:
    service.delete_url(session, code)
    return Response(status_code=204)
"""
DELETE_REPLACEMENT = """def delete_url(code: str, session: SessionDep) -> Response:
    service.delete_url(session, code)
    metadata_cache.invalidate(code)
    return Response(status_code=204)
"""


def apply_caching(workspace: Path) -> list[str]:
    changed: list[str] = []
    cache_path = workspace / "app" / "shortener" / "metadata_cache.py"
    routes = workspace / "app" / "shortener" / "routes.py"
    test_path = workspace / "tests" / "test_caching.py"
    if not cache_path.exists():
        cache_path.write_text(CACHE_MODULE, encoding="utf-8")
        changed.append("app/shortener/metadata_cache.py")
    text = routes.read_text(encoding="utf-8")
    if "metadata_cache" not in text:
        text = text.replace(
            "from app.shortener import service\n",
            "from app.shortener import service\n" + GET_IMPORT,
            1,
        )
        if GET_NEEDLE not in text or SET_NEEDLE not in text or DELETE_NEEDLE not in text:
            raise RuntimeError("caching adapter could not find metadata/delete handlers to patch")
        text = text.replace(GET_NEEDLE, GET_REPLACEMENT, 1)
        text = text.replace(SET_NEEDLE, SET_REPLACEMENT, 1)
        text = text.replace(DELETE_NEEDLE, DELETE_REPLACEMENT, 1)
        routes.write_text(text, encoding="utf-8")
        changed.append("app/shortener/routes.py")
    if not test_path.exists():
        test_path.write_text(TEST_SRC, encoding="utf-8")
        changed.append("tests/test_caching.py")
    return changed
