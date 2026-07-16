import logging

from iaai_scraper.logging_utils import (
    AccessLogTokenRedactor,
    install_access_log_redaction,
)


def _format(record: logging.LogRecord) -> str:
    return record.getMessage()


def test_access_log_redactor_scrubs_api_key_in_args():
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s"',
        args=("127.0.0.1:9", "GET /lots/100/thumbnail?api_key=sekrit HTTP/1.1"),
        exc_info=None,
    )
    assert AccessLogTokenRedactor().filter(record) is True
    formatted = _format(record)
    assert "REDACTED" in formatted
    assert "sekrit" not in formatted
    assert "api_key=REDACTED" in formatted


def test_access_log_redactor_scrubs_api_key_in_msg():
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='127.0.0.1 - "GET /lots/100/thumbnail?api_key=sekrit&x=1 HTTP/1.1" 200',
        args=(),
        exc_info=None,
    )
    AccessLogTokenRedactor().filter(record)
    assert "sekrit" not in record.msg
    assert "api_key=REDACTED" in record.msg
    assert "&x=1" in record.msg


def test_access_log_redactor_handles_uvicorn_style_tuple():
    # uvicorn: '%s - "%s %s HTTP/%s" %d' with path+query as one arg
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:54321",
            "GET",
            "/lots/100/thumbnail?api_key=sekrit",
            "1.1",
            200,
        ),
        exc_info=None,
    )
    AccessLogTokenRedactor().filter(record)
    formatted = _format(record)
    assert "sekrit" not in formatted
    assert "/lots/100/thumbnail?api_key=REDACTED" in formatted


def test_install_access_log_redaction_is_idempotent():
    name = "test.uvicorn.access.redaction"
    logger = logging.getLogger(name)
    logger.filters.clear()
    first = install_access_log_redaction(name)
    second = install_access_log_redaction(name)
    assert first is second
    assert sum(isinstance(f, AccessLogTokenRedactor) for f in logger.filters) == 1
    logger.filters.clear()
