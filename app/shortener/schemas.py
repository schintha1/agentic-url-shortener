from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class ShortenRequest(BaseModel):
    url: HttpUrl
    custom_alias: str | None = Field(
        default=None,
        min_length=4,
        max_length=32,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    ttl_seconds: int | None = Field(default=None, ge=60, le=31_536_000)


class ShortenResponse(BaseModel):
    code: str
    short_url: str
    original_url: str
    expires_at: datetime | None = None


class UrlMetadata(BaseModel):
    code: str
    original_url: str
    created_at: datetime
    expires_at: datetime | None = None
