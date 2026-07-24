"""真实租户数据库角色矩阵的失败关闭配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import unquote, urlparse


class TenantMatrixConfigError(RuntimeError):
    """测试数据库矩阵配置不安全或不完整。"""


@dataclass(frozen=True)
class TenantRoleMatrixConfig:
    admin_url: str
    runtime_url: str
    wecom_url: str
    worker_url: str
    database_name: str

    @classmethod
    def from_mapping(
        cls, values: Mapping[str, str],
    ) -> "TenantRoleMatrixConfig":
        if values.get("RUN_TENANT_DB_MATRIX") != "1":
            raise TenantMatrixConfigError("RUN_TENANT_DB_MATRIX=1_REQUIRED")
        keys = (
            "TENANT_TEST_DB_ADMIN_URL",
            "TENANT_TEST_DB_RUNTIME_URL",
            "TENANT_TEST_DB_WECOM_URL",
            "TENANT_TEST_DB_WORKER_URL",
        )
        if any(not values.get(key) for key in keys):
            raise TenantMatrixConfigError("TENANT_TEST_DATABASE_URLS_REQUIRED")
        urls = [values[key] for key in keys]
        parsed = [urlparse(url) for url in urls]
        if any(
            item.scheme not in {"postgres", "postgresql"} or not item.hostname
            for item in parsed
        ):
            raise TenantMatrixConfigError("TENANT_TEST_DATABASE_URLS_INVALID")
        identities = {
            (item.hostname, item.port or 5432, item.path.lstrip("/"))
            for item in parsed
        }
        if len(identities) != 1:
            raise TenantMatrixConfigError("TENANT_TEST_DATABASES_MUST_MATCH")
        database_name = unquote(parsed[0].path.lstrip("/"))
        acknowledgement = values.get("TENANT_TEST_DB_NAME_ACK", "")
        if (
            not database_name
            or "test" not in database_name.lower()
            or acknowledgement != database_name
        ):
            raise TenantMatrixConfigError(
                "TENANT_TEST_DATABASE_NAME_ACK_REQUIRED",
            )
        expected_roles = (
            "everydayai_runtime",
            "everydayai_wecom_runtime",
            "everydayai_worker",
        )
        actual_roles = tuple(unquote(item.username or "") for item in parsed[1:])
        if actual_roles != expected_roles:
            raise TenantMatrixConfigError("TENANT_TEST_DATABASE_ROLES_INVALID")
        return cls(*urls, database_name)
