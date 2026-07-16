"""Logging helpers for the private API / CLI.

Keeps secrets out of uvicorn access logs when media URLs carry ``?api_key=``.
"""
from __future__ import annotations

import logging
import re
from typing import Any

# Match api_key or sig query values until the next delimiter (&, space, quote, or EOL).
_SECRET_QUERY_RE = re.compile(
    r"((?:api_key|sig)=)([^&\s\"']+)",
    re.IGNORECASE,
)


class AccessLogTokenRedactor(logging.Filter):
    """Rewrite ``api_key=<token>`` / ``sig=<hmac>`` to ``…=REDACTED`` on access logs.

    Uvicorn puts the request path (with query string) in ``record.args``; other
    formats may put it in ``record.msg``. Both are scrubbed in place.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and _SECRET_QUERY_RE.search(record.msg):
            record.msg = _redact_secrets(record.msg)
        if record.args:
            record.args = _redact_args(record.args)
        return True


def _redact_secrets(text: str) -> str:
    return _SECRET_QUERY_RE.sub(r"\1REDACTED", text)


def _redact_args(args: Any) -> Any:
    if isinstance(args, tuple):
        return tuple(_redact_arg(a) for a in args)
    if isinstance(args, list):
        return [_redact_arg(a) for a in args]
    if isinstance(args, dict):
        return {k: _redact_arg(v) for k, v in args.items()}
    return _redact_arg(args)


def _redact_arg(value: Any) -> Any:
    if isinstance(value, str) and _SECRET_QUERY_RE.search(value):
        return _redact_secrets(value)
    return value


def install_access_log_redaction(logger_name: str = "uvicorn.access") -> AccessLogTokenRedactor:
    """Attach the redactor to ``uvicorn.access`` (idempotent)."""
    logger = logging.getLogger(logger_name)
    for existing in logger.filters:
        if isinstance(existing, AccessLogTokenRedactor):
            return existing
    redactor = AccessLogTokenRedactor()
    logger.addFilter(redactor)
    return redactor
