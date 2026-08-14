"""Control-plane authentication.

The SDLC API can approve releases, so it is the most sensitive surface in the
system. Auth is enforced whenever a key is configured; leaving it unset is a
documented local-demo convenience, not a default for deployment.
"""

import secrets
from typing import Annotated

from fastapi import Depends, Header, Request

from app.errors import AppError

API_KEY_HEADER = "X-API-Key"


def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias=API_KEY_HEADER)] = None,
) -> None:
    """Reject the request when a key is configured and the header does not match."""

    configured = getattr(request.app.state.settings, "sdlc_api_key", "") or ""
    if not configured:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, configured):
        raise AppError(401, "unauthorized", "A valid X-API-Key header is required")


ApiKeyGuard = Depends(require_api_key)
