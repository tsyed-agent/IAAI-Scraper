"""API authentication for the read/command surface.

When enabled, every route except ``GET /healthz`` requires a valid token via
``Authorization: Bearer <token>`` or ``X-API-Key: <token>``.

Set ``IAAI_API_TOKEN`` to the secret. Set ``IAAI_REQUIRE_AUTH=true`` (Docker
default) to reject startup and all requests without a token.
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException, Query

log = logging.getLogger("iaai.auth")


def api_token() -> Optional[str]:
    token = os.getenv("IAAI_API_TOKEN", "").strip()
    return token or None


def command_token() -> Optional[str]:
    """Token for mutating command routes; defaults to the read API token."""
    token = os.getenv("IAAI_COMMAND_TOKEN", "").strip()
    return token or api_token()


def require_auth_enabled() -> bool:
    """True when requests must present ``IAAI_API_TOKEN``."""
    mode = os.getenv("IAAI_REQUIRE_AUTH", "auto").strip().lower()
    if mode in ("1", "true", "yes", "on"):
        return True
    if mode in ("0", "false", "no", "off"):
        return False
    # auto: enforce whenever a token is configured
    return api_token() is not None


def validate_startup_auth() -> None:
    """Fail fast when auth is mandatory but no token is configured."""
    if not require_auth_enabled():
        if api_token():
            log.info("API auth enabled (token set, IAAI_REQUIRE_AUTH=auto)")
        else:
            log.warning(
                "API auth disabled — set IAAI_API_TOKEN and IAAI_REQUIRE_AUTH=true "
                "before exposing this service"
            )
        return
    if not api_token():
        raise RuntimeError(
            "IAAI_REQUIRE_AUTH is enabled but IAAI_API_TOKEN is not set"
        )
    log.info("API auth required on all routes except GET /healthz")


def _extract_token(
    authorization: Optional[str],
    x_api_key: Optional[str],
) -> Optional[str]:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return None


def verify_request_token(
    authorization: Optional[str],
    x_api_key: Optional[str],
    *,
    expected: Optional[str] = None,
) -> None:
    """Raise HTTPException when the request token is missing or invalid."""
    expected = expected or api_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="API auth misconfigured: IAAI_API_TOKEN is not set",
        )
    provided = _extract_token(authorization, x_api_key)
    if not provided:
        raise HTTPException(
            status_code=401,
            detail="missing API token (use Authorization: Bearer <token> or X-API-Key)",
        )
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="invalid API token")


def require_api_auth(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> None:
    if not require_auth_enabled():
        return
    verify_request_token(authorization, x_api_key)


def require_api_auth_flexible(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    api_key: Optional[str] = Query(
        None,
        description="Optional token for <img src> (thumbnail route only)",
    ),
) -> None:
    """Like ``require_api_auth`` but also accepts ``?api_key=`` for media URLs."""
    if not require_auth_enabled():
        return
    # Prefer headers; fall back to query so browsers can load authenticated images.
    verify_request_token(authorization, x_api_key or api_key)


def require_command_auth(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> None:
    """Require the optional command token for crawler-triggering routes."""
    # A command-only deployment must still protect the resource-intensive
    # browser crawl even when the read API is intentionally public.
    if not require_auth_enabled() and command_token() is None:
        return
    verify_request_token(
        authorization,
        x_api_key,
        expected=command_token(),
    )


def require_readyz_auth(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> None:
    """Auth for ``GET /readyz``; optionally opened to unauthenticated probes.

    Load balancers and orchestrators usually cannot attach headers to health
    probes. ``IAAI_READYZ_PUBLIC=true`` exempts the route — it exposes only
    counts and timestamps, never lot data.
    """
    if os.getenv("IAAI_READYZ_PUBLIC", "").strip().lower() in ("1", "true", "yes", "on"):
        return
    require_api_auth(authorization, x_api_key)
