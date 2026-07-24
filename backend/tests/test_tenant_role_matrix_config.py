"""租户真实角色矩阵的数据库安全门禁测试。"""

import pytest

from testing.tenant_role_matrix import (
    TenantMatrixConfigError,
    TenantRoleMatrixConfig,
)


def _values(database: str = "everydayai_test") -> dict[str, str]:
    base = f"localhost:5432/{database}"
    return {
        "RUN_TENANT_DB_MATRIX": "1",
        "TENANT_TEST_DB_NAME_ACK": database,
        "TENANT_TEST_DB_ADMIN_URL": f"postgresql://admin:secret@{base}",
        "TENANT_TEST_DB_RUNTIME_URL": (
            f"postgresql://everydayai_runtime:secret@{base}"
        ),
        "TENANT_TEST_DB_WECOM_URL": (
            f"postgresql://everydayai_wecom_runtime:secret@{base}"
        ),
        "TENANT_TEST_DB_WORKER_URL": (
            f"postgresql://everydayai_worker:secret@{base}"
        ),
    }


def test_matrix_requires_explicit_execution_gate() -> None:
    values = _values()
    values.pop("RUN_TENANT_DB_MATRIX")

    with pytest.raises(TenantMatrixConfigError, match="MATRIX=1"):
        TenantRoleMatrixConfig.from_mapping(values)


def test_matrix_rejects_database_without_test_name() -> None:
    values = _values("everydayai")

    with pytest.raises(TenantMatrixConfigError, match="NAME_ACK"):
        TenantRoleMatrixConfig.from_mapping(values)


def test_matrix_rejects_non_postgresql_url() -> None:
    values = _values()
    values["TENANT_TEST_DB_ADMIN_URL"] = "https://admin@localhost/matrix_test"

    with pytest.raises(TenantMatrixConfigError, match="URLS_INVALID"):
        TenantRoleMatrixConfig.from_mapping(values)


def test_matrix_rejects_mismatched_database_targets() -> None:
    values = _values()
    values["TENANT_TEST_DB_WORKER_URL"] = values[
        "TENANT_TEST_DB_WORKER_URL"
    ].replace("everydayai_test", "other_test")

    with pytest.raises(TenantMatrixConfigError, match="MUST_MATCH"):
        TenantRoleMatrixConfig.from_mapping(values)


def test_matrix_rejects_wrong_login_roles() -> None:
    values = _values()
    values["TENANT_TEST_DB_RUNTIME_URL"] = values[
        "TENANT_TEST_DB_RUNTIME_URL"
    ].replace("everydayai_runtime", "everydayai")

    with pytest.raises(TenantMatrixConfigError, match="ROLES_INVALID"):
        TenantRoleMatrixConfig.from_mapping(values)


def test_matrix_accepts_explicit_isolated_role_urls() -> None:
    config = TenantRoleMatrixConfig.from_mapping(_values())

    assert config.database_name == "everydayai_test"
    assert "everydayai_runtime" in config.runtime_url
