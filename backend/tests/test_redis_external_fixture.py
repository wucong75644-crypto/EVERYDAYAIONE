"""Fail-closed configuration tests for the real Redis fixture."""

from __future__ import annotations

import pytest

from tests.redis_external import _validated_test_url


def test_external_redis_requires_explicit_run_flag(monkeypatch) -> None:
    monkeypatch.delenv("RUN_REDIS_EXTERNAL_TESTS", raising=False)
    monkeypatch.setenv("REDIS_TEST_URL", "redis://127.0.0.1:16379/15")
    with pytest.raises(pytest.skip.Exception):
        _validated_test_url()


@pytest.mark.parametrize(
    "url",
    (
        "",
        "redis://redis.example.com:6379/15",
        "redis://127.0.0.1/15",
        "http://127.0.0.1:16379/15",
    ),
)
def test_external_redis_rejects_unsafe_url(monkeypatch, url: str) -> None:
    monkeypatch.setenv("RUN_REDIS_EXTERNAL_TESTS", "1")
    monkeypatch.setenv("REDIS_TEST_URL", url)
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(pytest.fail.Exception):
        _validated_test_url()


def test_external_redis_rejects_project_url(monkeypatch) -> None:
    url = "redis://127.0.0.1:16379/15"
    monkeypatch.setenv("RUN_REDIS_EXTERNAL_TESTS", "1")
    monkeypatch.setenv("REDIS_TEST_URL", url)
    monkeypatch.setenv("REDIS_URL", url)
    with pytest.raises(pytest.fail.Exception):
        _validated_test_url()


def test_external_redis_rejects_project_endpoint_across_schemes(
    monkeypatch,
) -> None:
    monkeypatch.setenv("RUN_REDIS_EXTERNAL_TESTS", "1")
    monkeypatch.setenv(
        "REDIS_TEST_URL", "redis://127.0.0.1:16379/15",
    )
    monkeypatch.setenv(
        "REDIS_URL", "rediss://127.0.0.1:16379/15",
    )
    with pytest.raises(pytest.fail.Exception):
        _validated_test_url()
