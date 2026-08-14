import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.errors import AppError
from app.shortener.codes import generate_code
from app.shortener.models import Click, Url
from app.shortener.validation import assert_safe_url

MAX_COLLISION_RETRIES = 5
HEADER_MAX = 512
logger = logging.getLogger(__name__)


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


def _truncate(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:HEADER_MAX]


def record_click(session: Session, code: str, referrer: str | None, user_agent: str | None) -> None:
    try:
        session.add(
            Click(
                url_code=code,
                referrer=_truncate(referrer),
                user_agent=_truncate(user_agent),
                accessed_at=utcnow(),
            )
        )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("click_record_failed", extra={"code": code})


def get_stats(session: Session, code: str) -> dict[str, object]:
    get_url(session, code)
    click_count = session.scalar(
        select(func.count()).select_from(Click).where(Click.url_code == code)
    )
    last_access = session.scalar(
        select(func.max(Click.accessed_at)).where(Click.url_code == code)
    )
    top_referrers = session.execute(
        select(Click.referrer, func.count().label("count"))
        .where(Click.url_code == code, Click.referrer.is_not(None))
        .group_by(Click.referrer)
        .order_by(desc("count"))
        .limit(5)
    ).all()
    top_uas = session.execute(
        select(Click.user_agent, func.count().label("count"))
        .where(Click.url_code == code, Click.user_agent.is_not(None))
        .group_by(Click.user_agent)
        .order_by(desc("count"))
        .limit(5)
    ).all()
    return {
        "code": code,
        "clicks": int(click_count or 0),
        "last_access": last_access,
        "top_referrers": [{"value": row[0], "count": row[1]} for row in top_referrers],
        "top_user_agents": [{"value": row[0], "count": row[1]} for row in top_uas],
    }
