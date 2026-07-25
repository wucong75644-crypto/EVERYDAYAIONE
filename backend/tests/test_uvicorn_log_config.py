"""Uvicorn 最终日志配置必须安装访问 Token 脱敏过滤器。"""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UNIT = (ROOT / "deploy/everydayai-backend.service").read_text()
LOG_CONFIG = json.loads(
    (ROOT / "backend/uvicorn-log-config.json").read_text()
)


def test_backend_service_uses_canonical_log_config() -> None:
    assert (
        "--log-config /var/www/everydayai/backend/uvicorn-log-config.json"
        in UNIT
    )


def test_all_uvicorn_handlers_install_token_filter() -> None:
    filter_config = LOG_CONFIG["filters"]["redact_access_token"]
    assert (
        filter_config["()"]
        == "core.logging_config.UvicornAccessTokenFilter"
    )
    assert LOG_CONFIG["handlers"]["access"]["filters"] == [
        "redact_access_token"
    ]
    assert LOG_CONFIG["handlers"]["default"]["filters"] == [
        "redact_access_token"
    ]
