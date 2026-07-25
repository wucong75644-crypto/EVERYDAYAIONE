"""Uvicorn access log 的认证查询参数脱敏合同。"""

import logging

from core.logging_config import (
    _UvicornAccessTokenFilter,
    _install_access_token_filter,
)


def test_access_filter_redacts_token_and_preserves_other_query_params() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1",
            "GET",
            "/api/ws?token=secret.jwt&org_id=org-1",
            "1.1",
            403,
        ),
        None,
    )

    assert _UvicornAccessTokenFilter().filter(record)
    assert record.args[2] == "/api/ws?token=***&org_id=org-1"
    assert "secret.jwt" not in record.getMessage()


def test_access_filter_installation_is_idempotent() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    original_filters = list(access_logger.filters)
    access_logger.filters = [
        item for item in original_filters
        if not isinstance(item, _UvicornAccessTokenFilter)
    ]
    try:
        _install_access_token_filter()
        _install_access_token_filter()
        assert sum(
            isinstance(item, _UvicornAccessTokenFilter)
            for item in access_logger.filters
        ) == 1
    finally:
        access_logger.filters = original_filters
