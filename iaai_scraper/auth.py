"""API authentication for the read/command surface.

When enabled, every route except ``GET /healthz`` requires a valid token via
``Authorization: Bearer <token>`` or ``X-API-Key: <token>``.

Thumbnail routes also accept short-lived HMAC-signed query params
(``?expires=&sig=``) so ``<img src>`` works without embedding the long-lived
API token. ``?api_key=`` remains accepted for one release (deprecated).

Set ``IAAI_API_TOKEN`` to the secret. Set ``IAAI_REQUIRE_AUTH=true`` (Docker
default) to reject startup and all requests without a token.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Optional, Union

from fastapi import Header, HTTPException, Query, Request

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


def sign_media_path(
    path: str,
    expires_at: int,
    *,
    key: Optional[str] = None,
) -> str:
    """HMAC-SHA256 hex digest over ``{path}:{expires_at}`` using the API token."""
    secret = (key if key is not None else api_token()) or ""
    if not secret:
        raise ValueError("cannot sign media path without IAAI_API_TOKEN")
    msg = f"{path}:{int(expires_at)}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_media_signature(
    path: str,
    expires_at: Union[int, str, None],
    signature: Optional[str],
    *,
    key: Optional[str] = None,
    now: Optional[float] = None,
) -> bool:
    """True when signature matches and ``expires_at`` is still in the future."""
    if signature is None or expires_at is None:
        return False
    try:
        exp = int(expires_at)
    except (TypeError, ValueError):
        return False
    if exp <= int(now if now is not None else time.time()):
        return False
    try:
        expected = sign_media_path(path, exp, key=key)
    except ValueError:
        return False
    return secrets.compare_digest(expected, signature)


def signed_media_cache_control(
    expires_at: Optional[Union[int, str]],
    *,
    now: Optional[float] = None,
    base: Optional[str] = None,
) -> str:
    """Cap cache freshness at the remaining lifetime of a signed URL."""
    from . import config

    cache_control = base if base is not None else config.IMAGE_CACHE_CONTROL
    try:
        remaining = int(expires_at) - int(now if now is not None else time.time())
    except (TypeError, ValueError):
        return "no-store"
    if remaining <= 0:
        return "no-store"
    if any(
        directive.strip().lower() == "no-store"
        for directive in cache_control.split(",")
    ):
        return cache_control

    directives = []
    for directive in cache_control.split(","):
        directive = directive.strip()
        name, _, value = directive.partition("=")
        normalized = name.strip().lower()
        if normalized in {"stale-while-revalidate", "stale-if-error"}:
            continue
        if normalized in {"max-age", "s-maxage"}:
            try:
                value = str(min(int(value.strip()), remaining))
            except ValueError:
                value = str(remaining)
            directive = f"{name.strip()}={value}"
        directives.append(directive)
    present = {
        directive.split("=", 1)[0].strip().lower()
        for directive in directives
    }
    for name in ("max-age", "s-maxage"):
        if name not in present:
            directives.append(f"{name}={remaining}")
    return ", ".join(directive for directive in directives if directive)


def media_signed_href(path: str, *, ttl_s: Optional[int] = None) -> str:
    """Build ``path?expires=…&sig=…`` when API auth is on; else bare path."""
    from . import config

    token = api_token()
    if not token or not require_auth_enabled():
        return path
    ttl = int(ttl_s if ttl_s is not None else config.MEDIA_URL_TTL_S)
    expires_at = int(time.time()) + max(ttl, 1)
    sig = sign_media_path(path, expires_at, key=token)
    return f"{path}?expires={expires_at}&sig={sig}"


def require_api_auth_flexible(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    api_key: Optional[str] = Query(
        None,
        description="Deprecated: long-lived token for <img src>; prefer expires+sig",
    ),
    expires: Optional[str] = Query(
        None,
        description="Unix expiry for signed media URL (thumbnail route)",
    ),
    sig: Optional[str] = Query(
        None,
        description="HMAC signature for signed media URL (thumbnail route)",
    ),
) -> str:
    """Auth for media: header token, signed ``expires``+``sig``, or deprecated ``api_key``."""
    if not require_auth_enabled():
        return "unauthenticated"

    expected = api_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="API auth misconfigured: IAAI_API_TOKEN is not set",
        )

    provided = _extract_token(authorization, x_api_key or api_key)
    if provided and secrets.compare_digest(provided, expected):
        if expires is not None or sig:
            if verify_media_signature(request.url.path, expires, sig, key=expected):
                return "signed"
            return "signed-invalid"
        return "header"

    if expires is not None or sig:
        if verify_media_signature(request.url.path, expires, sig, key=expected):
            return "signed"
        raise HTTPException(
            status_code=401,
            detail="invalid or expired media signature",
        )

    if provided:
        raise HTTPException(status_code=403, detail="invalid API token")
    raise HTTPException(
        status_code=401,
        detail="missing API token or media signature "
        "(use Authorization / X-API-Key, or ?expires=&sig=)",
    )


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
