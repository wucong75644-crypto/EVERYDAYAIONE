"""Read-only legacy configuration fact collector tests."""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from core.crypto import aes_encrypt, generate_encrypt_key
from services.configuration.legacy_migration import (
    LegacyConfigurationFactCollector,
    LegacyPreflightCollectionError,
)


ORG_ID = "00000000-0000-0000-0000-000000000010"


class FakeQuery:
    def __init__(self, result: object) -> None:
        self._result = result
        self.columns = ""

    def select(self, columns: str) -> "FakeQuery":
        self.columns = columns
        return self

    def execute(self) -> SimpleNamespace:
        if isinstance(self._result, Exception):
            raise self._result
        return SimpleNamespace(data=self._result)


class FakeDB:
    def __init__(self, tables: dict[str, object]) -> None:
        self.tables = tables
        self.calls: list[tuple[str, FakeQuery]] = []

    def table(self, name: str) -> FakeQuery:
        query = FakeQuery(self.tables.get(name, []))
        self.calls.append((name, query))
        return query


def _tables(
    key: str,
    *,
    organization_corp_id: str = "corp-1",
    encrypted_corp_id: str = "corp-1",
) -> dict[str, object]:
    return {
        "organizations": [{
            "id": ORG_ID,
            "wecom_corp_id": organization_corp_id,
            "encrypt_key": key,
        }],
        "org_configs": [
            {
                "org_id": ORG_ID,
                "config_key": "kuaimai_app_key",
                "config_value_encrypted": aes_encrypt(
                    "PLAINTEXT_MARKER_APP",
                    key,
                ),
            },
            {
                "org_id": ORG_ID,
                "config_key": "kuaimai_app_secret",
                "config_value_encrypted": aes_encrypt(
                    "PLAINTEXT_MARKER_SECRET",
                    key,
                ),
            },
            {
                "org_id": ORG_ID,
                "config_key": "wecom_corp_id",
                "config_value_encrypted": aes_encrypt(
                    encrypted_corp_id,
                    key,
                ),
            },
        ],
        "kuaimai_external_credentials": [{
            "org_id": ORG_ID,
            "source": "thinktank",
            "status": "expired",
            "censeid_cookie": (
                f"enc:{aes_encrypt('PLAINTEXT_MARKER_COOKIE', key)}"
            ),
            "cookie_full": (
                f"enc:{aes_encrypt('PLAINTEXT_MARKER_FULL', key)}"
            ),
        }],
    }


def _item(result, target_key: str):
    return next(
        item
        for item in result[0].report.items
        if item.target_key == target_key
    )


def test_collector_batch_reads_three_tables_and_returns_no_values() -> None:
    key = generate_encrypt_key()
    db = FakeDB(_tables(key))

    result = LegacyConfigurationFactCollector(
        db,
        global_encrypt_key=None,
    ).collect()

    assert result[0].org_id == ORG_ID
    assert result[0].report.can_migrate is True
    assert _item(result, "erp.app_credentials").status == "ready"
    external = _item(result, "kuaimai_external.thinktank.cookie")
    assert external.status == "ready"
    assert external.target_enabled is False
    assert [name for name, _ in db.calls] == [
        "organizations",
        "org_configs",
        "kuaimai_external_credentials",
    ]
    serialized = str(asdict(result[0]))
    for value in (
        "PLAINTEXT_MARKER_APP",
        "PLAINTEXT_MARKER_SECRET",
        "PLAINTEXT_MARKER_COOKIE",
        "PLAINTEXT_MARKER_FULL",
    ):
        assert value not in serialized


def test_collector_uses_global_key_when_org_key_is_missing() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["organizations"][0]["encrypt_key"] = None

    result = LegacyConfigurationFactCollector(
        FakeDB(tables),
        global_encrypt_key=key,
    ).collect()

    assert result[0].report.can_migrate is True
    assert _item(result, "erp.app_credentials").status == "ready"


def test_unreadable_legacy_config_blocks_without_raising_secret_error() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["org_configs"][0]["config_value_encrypted"] = "damaged"

    result = LegacyConfigurationFactCollector(
        FakeDB(tables),
        global_encrypt_key=None,
    ).collect()

    item = _item(result, "erp.app_credentials")
    assert result[0].report.can_migrate is False
    assert item.status == "invalid"
    assert item.reason == "LEGACY_SOURCE_UNREADABLE"


def test_corp_id_mismatch_blocks_migration() -> None:
    key = generate_encrypt_key()
    result = LegacyConfigurationFactCollector(
        FakeDB(_tables(key, encrypted_corp_id="other-corp")),
        global_encrypt_key=None,
    ).collect()

    assert result[0].report.can_migrate is False
    assert _item(result, "wecom.corp_id").status == "conflict"


def test_unreadable_encrypted_cookie_blocks_migration() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["kuaimai_external_credentials"][0][
        "censeid_cookie"
    ] = "enc:damaged"

    result = LegacyConfigurationFactCollector(
        FakeDB(tables),
        global_encrypt_key=None,
    ).collect()

    item = _item(result, "kuaimai_external.thinktank.cookie")
    assert result[0].report.can_migrate is False
    assert item.status == "invalid"
    assert item.reason == "LEGACY_EXTERNAL_UNREADABLE"


def test_plaintext_cookie_is_reported_without_attempting_migration() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["kuaimai_external_credentials"][0][
        "censeid_cookie"
    ] = "legacy-cookie"

    result = LegacyConfigurationFactCollector(
        FakeDB(tables),
        global_encrypt_key=None,
    ).collect()

    item = _item(result, "kuaimai_external.thinktank.cookie")
    assert item.status == "conflict"
    assert item.reason == "LEGACY_EXTERNAL_PLAINTEXT_REJECTED"


def test_missing_full_cookie_blocks_as_incomplete() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["kuaimai_external_credentials"][0]["cookie_full"] = None

    result = LegacyConfigurationFactCollector(
        FakeDB(tables),
        global_encrypt_key=None,
    ).collect()

    item = _item(result, "kuaimai_external.thinktank.cookie")
    assert result[0].report.can_migrate is False
    assert item.status == "incomplete"
    assert item.reason == "LEGACY_EXTERNAL_SOURCE_INCOMPLETE"


def test_orphan_legacy_row_fails_closed() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["org_configs"][0]["org_id"] = (
        "00000000-0000-0000-0000-000000000099"
    )

    with pytest.raises(
        LegacyPreflightCollectionError,
        match="LEGACY_ORPHAN_ROW:org_configs",
    ):
        LegacyConfigurationFactCollector(
            FakeDB(tables),
            global_encrypt_key=None,
        ).collect()


def test_database_read_failure_has_stable_table_code() -> None:
    db = FakeDB({"organizations": RuntimeError("connection detail")})

    with pytest.raises(
        LegacyPreflightCollectionError,
        match="LEGACY_READ_FAILED:organizations",
    ):
        LegacyConfigurationFactCollector(
            db,
            global_encrypt_key=None,
        ).collect()


def test_missing_both_encryption_keys_marks_configs_unreadable() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["organizations"][0]["encrypt_key"] = None

    result = LegacyConfigurationFactCollector(
        FakeDB(tables),
        global_encrypt_key=None,
    ).collect()

    assert result[0].report.can_migrate is False
    assert _item(result, "erp.app_credentials").status == "invalid"


def test_malformed_base64_key_is_normalized_to_unreadable() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["organizations"][0]["encrypt_key"] = "a"

    result = LegacyConfigurationFactCollector(
        FakeDB(tables),
        global_encrypt_key=None,
    ).collect()

    assert result[0].report.can_migrate is False
    assert _item(result, "erp.app_credentials").status == "invalid"


def test_invalid_external_source_fails_closed() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["kuaimai_external_credentials"][0]["source"] = "unknown"

    with pytest.raises(
        LegacyPreflightCollectionError,
        match="LEGACY_EXTERNAL_ROW_INVALID",
    ):
        LegacyConfigurationFactCollector(
            FakeDB(tables),
            global_encrypt_key=None,
        ).collect()


def test_non_list_database_response_fails_closed() -> None:
    db = FakeDB({"organizations": {"id": ORG_ID}})

    with pytest.raises(
        LegacyPreflightCollectionError,
        match="LEGACY_RESPONSE_INVALID:organizations",
    ):
        LegacyConfigurationFactCollector(
            db,
            global_encrypt_key=None,
        ).collect()


def test_duplicate_organization_fails_closed() -> None:
    key = generate_encrypt_key()
    organization = _tables(key)["organizations"][0]
    db = FakeDB({
        "organizations": [organization, dict(organization)],
    })

    with pytest.raises(
        LegacyPreflightCollectionError,
        match="LEGACY_ORGANIZATION_DUPLICATE",
    ):
        LegacyConfigurationFactCollector(
            db,
            global_encrypt_key=None,
        ).collect()


def test_missing_required_row_field_fails_closed() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    del tables["organizations"][0]["id"]

    with pytest.raises(
        LegacyPreflightCollectionError,
        match="LEGACY_FIELD_INVALID:id",
    ):
        LegacyConfigurationFactCollector(
            FakeDB(tables),
            global_encrypt_key=None,
        ).collect()
