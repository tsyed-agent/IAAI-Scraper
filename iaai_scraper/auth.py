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

from fastapi import Header, HTTPException

log = logging.getLogger("iaai.auth")


def api_token() -> Optional[str]:
    token = os.getenv("IAAI_API_TOKEN", "").strip()
    return token or None


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
) -> None:
    """Raise HTTPException when the request token is missing or invalid."""
    expected = api_token()
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

