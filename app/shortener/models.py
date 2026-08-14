from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Url(Base):
    __tablename__ = "urls"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    original_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Click(Base):
    __tablename__ = "clicks"
    __table_args__ = (Index("ix_clicks_url_code", "url_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("urls.code", ondelete="CASCADE"), nullable=False
    )
    referrer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[str] = mapped_column(String(4096), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
