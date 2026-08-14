from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError
from app.shortener import service
from app.shortener.schemas import ShortenRequest, ShortenResponse, StatsResponse, UrlMetadata

router = APIRouter()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]
IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post("/v1/shorten", response_model=ShortenResponse, summary="Create a short URL")
def shorten(
    request: Request,
    body: ShortenRequest,
    settings: SettingsDep,
    session: SessionDep,
    idempotency_key: IdempotencyHeader = None,
) -> ShortenResponse:
    limiter = request.app.state.limiter
    client_ip = request.client.host if request.client else "unknown"
    if not limiter.allow(client_ip):
        raise AppError(
            429,
            "rate_limited",
            "Too many requests",
            headers={"Retry-After": str(limiter.window_seconds)},
        )
    payload = body.model_dump(mode="json")
    body_hash = service.hash_body(payload)
    if idempotency_key:
        cached = service.lookup_idempotency(session, idempotency_key, body_hash)
        if cached is not None:
            return ShortenResponse.model_validate_json(cached)
    record = service.create_short_url(
        session,
        original_url=str(body.url),
        base_url=settings.base_url,
        allow_private=settings.allow_private_targets,
        custom_alias=body.custom_alias,
        ttl_seconds=body.ttl_seconds,
    )
    response = ShortenResponse(
        code=record.code,
        short_url=service.short_url_for(settings.base_url, record.code),
        original_url=record.original_url,
        expires_at=record.expires_at,
    )
    if idempotency_key:
        service.store_idempotency(session, idempotency_key, body_hash, response.model_dump_json())
    return response


@router.get("/v1/urls/{code}", response_model=UrlMetadata, summary="Get short URL metadata")
def get_metadata(
    code: str,
    session: SessionDep,
) -> UrlMetadata:
    record = service.get_url(session, code)
    return UrlMetadata(
        code=record.code,
        original_url=record.original_url,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


@router.get("/v1/urls/{code}/stats", response_model=StatsResponse, summary="Click analytics")
def get_stats(code: str, session: SessionDep) -> StatsResponse:
    payload = service.get_stats(session, code)
    return StatsResponse.model_validate(payload)


@router.get("/{code}", summary="Redirect a short code")
def redirect(request: Request, code: str, session: SessionDep) -> RedirectResponse:
    record = service.get_url(session, code)
    service.record_click(
        session,
        record.code,
        referrer=request.headers.get("referer"),
        user_agent=request.headers.get("user-agent"),
    )
    return RedirectResponse(url=record.original_url, status_code=302)
