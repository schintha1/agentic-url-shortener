from ipaddress import ip_address
from urllib.parse import urlparse

from app.errors import AppError

ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_SCHEMES = {"javascript", "file", "data", "vbscript"}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}


def assert_safe_url(url: str, allow_private: bool) -> None:
    """Reject unsafe schemes, credentials in the URL, and private hosts when disallowed."""

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme in BLOCKED_SCHEMES or scheme not in ALLOWED_SCHEMES:
        raise AppError(422, "url_unsafe", "URL scheme is not allowed")
    if parsed.username or parsed.password:
        raise AppError(422, "url_unsafe", "URL must not contain userinfo")
    host = (parsed.hostname or "").lower()
    if not host:
        raise AppError(422, "url_unsafe", "URL host is required")
    if not allow_private and _is_private_host(host):
        raise AppError(422, "url_unsafe", "Private or loopback hosts are not allowed")


def _is_private_host(host: str) -> bool:
    if host in LOOPBACK_HOSTS or host.endswith(".local"):
        return True
    try:
        addr = ip_address(host)
    except ValueError:
        return False
    return bool(addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved)
