"""One-read legacy import source snapshot tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.crypto import aes_encrypt, generate_encrypt_key
from services.configuration.legacy_import_source import (
    LegacyImportSourceError,
    LegacyImportSourceReader,
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


def _tables(key: str) -> dict[str, object]:
    return {
        "organizations": [{
            "id": ORG_ID,
            "wecom_corp_id": "corp-1",
            "encrypt_key": key,
        }],
        "org_configs": [
            {
                "org_id": ORG_ID,
                "config_key": "kuaimai_app_key",
                "config_value_encrypted": aes_encrypt("APP_KEY", key),
            },
            {
                "org_id": ORG_ID,
                "config_key": "kuaimai_app_secret",
                "config_value_encrypted": aes_encrypt("APP_SECRET", key),
            },
            {
                "org_id": ORG_ID,
                "config_key": "wecom_corp_id",
                "config_value_encrypted": aes_encrypt("corp-1", key),
            },
        ],
        "kuaimai_external_credentials": [{
            "org_id": ORG_ID,
            "source": "thinktank",
            "status": "active",
            "kuaimai_company_id": 123,
            "censeid_cookie": f"enc:{aes_encrypt('CENSEID', key)}",
            "cookie_full": f"enc:{aes_encrypt('COOKIE_FULL', key)}",
        }],
    }


def _item(snapshot, target_key: str):
    return next(
        item
        for item in snapshot.preflight_reports[ORG_ID].items
        if item.target_key == target_key
    )


def test_reader_uses_exactly_three_reads_and_aligns_values_with_report() -> None:
    key = generate_encrypt_key()
    db = FakeDB(_tables(key))

    snapshot = LegacyImportSourceReader(
        db,
        global_encrypt_key=None,
    ).read()

    source = snapshot.organizations[0]
    assert source.config_values["kuaimai_app_secret"] == "APP_SECRET"
    assert source.external_credentials[0].cookie_full == "COOKIE_FULL"
    assert snapshot.preflight_reports[ORG_ID].can_migrate is True
    assert [name for name, _ in db.calls] == [
        "organizations",
        "org_configs",
        "kuaimai_external_credentials",
    ]
    assert "kuaimai_company_id" in db.calls[2][1].columns
    assert "APP_SECRET" not in repr(snapshot)


def test_reader_uses_global_key_only_when_org_key_is_absent() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["organizations"][0]["encrypt_key"] = None

    snapshot = LegacyImportSourceReader(
        FakeDB(tables),
        global_encrypt_key=key,
    ).read()

    assert snapshot.preflight_reports[ORG_ID].can_migrate is True


def test_damaged_config_and_cookie_produce_blocked_preflight() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["org_configs"][0]["config_value_encrypted"] = "damaged"
    tables["kuaimai_external_credentials"][0][
        "censeid_cookie"
    ] = "enc:damaged"

    snapshot = LegacyImportSourceReader(
        FakeDB(tables),
        global_encrypt_key=None,
    ).read()

    assert snapshot.preflight_reports[ORG_ID].can_migrate is False
    assert _item(snapshot, "erp.app_credentials").status == "invalid"
    assert _item(
        snapshot,
        "kuaimai_external.thinktank.cookie",
    ).status == "invalid"


def test_missing_keys_and_invalid_required_text_fail_closed() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["organizations"][0]["encrypt_key"] = None
    snapshot = LegacyImportSourceReader(
        FakeDB(tables),
        global_encrypt_key=None,
    ).read()
    assert snapshot.preflight_reports[ORG_ID].can_migrate is False

    tables = _tables(key)
    tables["organizations"][0]["id"] = ""
    with pytest.raises(
        LegacyImportSourceError,
        match="LEGACY_IMPORT_FIELD_INVALID:id",
    ):
        LegacyImportSourceReader(
            FakeDB(tables),
            global_encrypt_key=None,
        ).read()


def test_plaintext_or_missing_full_cookie_never_enters_value_snapshot() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    tables["kuaimai_external_credentials"][0]["cookie_full"] = None

    snapshot = LegacyImportSourceReader(
        FakeDB(tables),
        global_encrypt_key=None,
    ).read()

    external = snapshot.organizations[0].external_credentials[0]
    assert external.cookie_full == ""
    assert _item(
        snapshot,
        "kuaimai_external.thinktank.cookie",
    ).status == "incomplete"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("duplicate_config", "LEGACY_IMPORT_CONFIG_DUPLICATE"),
        ("duplicate_external", "LEGACY_IMPORT_EXTERNAL_DUPLICATE"),
        ("invalid_source", "LEGACY_IMPORT_EXTERNAL_INVALID"),
        ("invalid_company", "LEGACY_IMPORT_EXTERNAL_COMPANY_INVALID"),
        ("orphan_config", "LEGACY_IMPORT_ORPHAN_ROW:org_configs"),
        ("duplicate_org", "LEGACY_IMPORT_ORGANIZATION_DUPLICATE"),
    ],
)
def test_malformed_source_rows_fail_closed(
    mutation: str,
    code: str,
) -> None:
    key = generate_encrypt_key()
    tables = _tables(key)
    if mutation == "duplicate_config":
        tables["org_configs"].append(dict(tables["org_configs"][0]))
    elif mutation == "duplicate_external":
        tables["kuaimai_external_credentials"].append(
            dict(tables["kuaimai_external_credentials"][0])
        )
    elif mutation == "invalid_source":
        tables["kuaimai_external_credentials"][0]["source"] = "unknown"
    elif mutation == "invalid_company":
        tables["kuaimai_external_credentials"][0][
            "kuaimai_company_id"
        ] = True
    elif mutation == "orphan_config":
        tables["org_configs"][0]["org_id"] = (
            "00000000-0000-0000-0000-000000000099"
        )
    else:
        tables["organizations"].append(dict(tables["organizations"][0]))

    with pytest.raises(LegacyImportSourceError, match=code):
        LegacyImportSourceReader(
            FakeDB(tables),
            global_encrypt_key=None,
        ).read()


def test_read_failure_and_invalid_response_have_stable_codes() -> None:
    with pytest.raises(
        LegacyImportSourceError,
        match="LEGACY_IMPORT_READ_FAILED:organizations",
    ):
        LegacyImportSourceReader(
            FakeDB({"organizations": RuntimeError("connection detail")}),
            global_encrypt_key=None,
        ).read()

    with pytest.raises(
        LegacyImportSourceError,
        match="LEGACY_IMPORT_RESPONSE_INVALID:organizations",
    ):
        LegacyImportSourceReader(
            FakeDB({"organizations": "invalid"}),
            global_encrypt_key=None,
        ).read()


def test_export_requires_exact_three_array_shape() -> None:
    reader = LegacyImportSourceReader(
        None,
        global_encrypt_key=None,
    )

    with pytest.raises(
        LegacyImportSourceError,
        match="LEGACY_IMPORT_EXPORT_SHAPE_INVALID",
    ):
        reader.read_export({"organizations": []})

    with pytest.raises(
        LegacyImportSourceError,
        match="LEGACY_IMPORT_EXPORT_ROWS_INVALID:org_configs",
    ):
        reader.read_export({
            "organizations": [],
            "org_configs": "invalid",
            "external_credentials": [],
        })


def test_export_payload_reuses_the_same_strict_parser() -> None:
    key = generate_encrypt_key()
    tables = _tables(key)

    snapshot = LegacyImportSourceReader(
        None,
        global_encrypt_key=None,
    ).read_export({
        "organizations": tables["organizations"],
        "org_configs": tables["org_configs"],
        "external_credentials": tables[
            "kuaimai_external_credentials"
        ],
    })

    assert snapshot.preflight_reports[ORG_ID].can_migrate is True
    assert snapshot.organizations[0].config_values[
        "kuaimai_app_secret"
    ] == "APP_SECRET"
