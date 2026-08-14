from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.shortener.codes import generate_code
from app.shortener.models import Url
from app.shortener.validation import assert_safe_url

MAX_COLLISION_RETRIES = 5


def utcnow() -> datetime:
    return datetime.now(UTC)


def _is_expired(url: Url) -> bool:
    if url.expires_at is None:
        return False
    expires = url.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires <= utcnow()


def create_short_url(
    session: Session,
    *,
    original_url: str,
    base_url: str,
    allow_private: bool,
    custom_alias: str | None = None,
    ttl_seconds: int | None = None,
) -> Url:
    assert_safe_url(original_url, allow_private=allow_private)
    expires_at = utcnow() + timedelta(seconds=ttl_seconds) if ttl_seconds else None
    if custom_alias:
        record = Url(
            code=custom_alias,
            original_url=original_url,
            expires_at=expires_at,
            created_at=utcnow(),
        )
        session.add(record)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise AppError(409, "alias_conflict", "Custom alias is already in use") from exc
        session.refresh(record)
        return record

    last_error: IntegrityError | None = None
    for _ in range(MAX_COLLISION_RETRIES):
        record = Url(
            code=generate_code(),
            original_url=original_url,
            expires_at=expires_at,
            created_at=utcnow(),
        )
        session.add(record)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            last_error = exc
            continue
        session.refresh(record)
        return record
    raise AppError(500, "code_collision", "Unable to allocate a unique code") from last_error


def get_url(session: Session, code: str) -> Url:
    record = session.get(Url, code)
    if record is None:
        raise AppError(404, "not_found", "Short URL not found")
    if _is_expired(record):
        raise AppError(410, "expired", "Short URL has expired")
    return record


def short_url_for(base_url: str, code: str) -> str:
    return f"{base_url.rstrip('/')}/{code}"
