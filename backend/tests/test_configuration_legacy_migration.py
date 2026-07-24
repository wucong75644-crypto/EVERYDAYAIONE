"""Legacy configuration migration contract and preflight tests."""

from __future__ import annotations

from dataclasses import asdict

from services.configuration.legacy_migration import (
    LEGACY_TARGET_CONTRACTS,
    ExternalCredentialFact,
    build_legacy_preflight,
)


def _item(report, key: str):
    return next(item for item in report.items if item.target_key == key)


def test_contract_groups_atomic_secret_payloads() -> None:
    assert LEGACY_TARGET_CONTRACTS["erp.app_credentials"].source_keys == (
        "kuaimai_app_key",
        "kuaimai_app_secret",
    )
    assert LEGACY_TARGET_CONTRACTS["erp.token_pair"].source_keys == (
        "kuaimai_access_token",
        "kuaimai_refresh_token",
    )
    assert LEGACY_TARGET_CONTRACTS["wecom.bot_credentials"].source_keys == (
        "wecom_bot_id",
        "wecom_bot_secret",
    )


def test_complete_production_shape_is_ready_without_values() -> None:
    report = build_legacy_preflight(
        configured_keys={
            key
            for contract in LEGACY_TARGET_CONTRACTS.values()
            for key in contract.source_keys
        } | {"wecom_corp_id"},
        organization_corp_id_configured=True,
        corp_id_sources_match=True,
        external_credentials=(
            ExternalCredentialFact(
                "thinktank", "expired", True, True,
            ),
            ExternalCredentialFact(
                "viperp", "expired", True, True,
            ),
        ),
    )

    assert report.can_migrate is True
    assert report.unknown_keys == ()
    assert all(
        item.status in {"ready", "skipped"} for item in report.items
    )
    assert _item(
        report,
        "kuaimai_external.thinktank.cookie",
    ).target_enabled is False
    serialized = str(asdict(report))
    for secret in ("app-secret", "access-token", "cookie-value"):
        assert secret not in serialized


def test_partial_atomic_group_blocks_migration() -> None:
    report = build_legacy_preflight(
        configured_keys={"kuaimai_app_key"},
        organization_corp_id_configured=False,
        corp_id_sources_match=None,
    )

    item = _item(report, "erp.app_credentials")
    assert report.can_migrate is False
    assert item.status == "incomplete"
    assert item.source_keys == ("kuaimai_app_key",)


def test_dual_corp_id_requires_verified_equality() -> None:
    report = build_legacy_preflight(
        configured_keys={"wecom_corp_id"},
        organization_corp_id_configured=True,
        corp_id_sources_match=None,
    )

    assert report.can_migrate is False
    assert _item(report, "wecom.corp_id").reason == (
        "LEGACY_CORP_ID_COMPARISON_REQUIRED"
    )


def test_unreadable_encrypted_corp_id_blocks_migration() -> None:
    report = build_legacy_preflight(
        configured_keys={"wecom_corp_id"},
        organization_corp_id_configured=True,
        corp_id_sources_match=None,
        invalid_keys={"wecom_corp_id"},
    )

    item = _item(report, "wecom.corp_id")
    assert report.can_migrate is False
    assert item.status == "invalid"
    assert item.reason == "LEGACY_SOURCE_UNREADABLE"


def test_organization_corp_id_is_selected_when_it_is_the_only_source() -> None:
    report = build_legacy_preflight(
        configured_keys=set(),
        organization_corp_id_configured=True,
        corp_id_sources_match=None,
    )

    item = _item(report, "wecom.corp_id")
    assert report.can_migrate is True
    assert item.status == "ready"
    assert item.source_keys == ("organizations.wecom_corp_id",)


def test_encrypted_corp_id_is_selected_when_it_is_the_only_source() -> None:
    report = build_legacy_preflight(
        configured_keys={"wecom_corp_id"},
        organization_corp_id_configured=False,
        corp_id_sources_match=None,
    )

    item = _item(report, "wecom.corp_id")
    assert report.can_migrate is True
    assert item.status == "ready"
    assert item.source_keys == ("wecom_corp_id",)


def test_unknown_legacy_key_fails_closed() -> None:
    report = build_legacy_preflight(
        configured_keys={"unmapped_secret"},
        organization_corp_id_configured=False,
        corp_id_sources_match=None,
    )

    assert report.can_migrate is False
    assert report.unknown_keys == ("unmapped_secret",)


def test_plaintext_external_cookie_is_rejected() -> None:
    report = build_legacy_preflight(
        configured_keys=set(),
        organization_corp_id_configured=False,
        corp_id_sources_match=None,
        external_credentials=(
            ExternalCredentialFact("thinktank", "active", False, None),
        ),
    )

    item = _item(report, "kuaimai_external.thinktank.cookie")
    assert report.can_migrate is False
    assert item.status == "conflict"
    assert item.reason == "LEGACY_EXTERNAL_PLAINTEXT_REJECTED"


def test_external_cookie_without_full_cookie_is_incomplete() -> None:
    report = build_legacy_preflight(
        configured_keys=set(),
        organization_corp_id_configured=False,
        corp_id_sources_match=None,
        external_credentials=(
            ExternalCredentialFact("thinktank", "active", True, None),
        ),
    )

    item = _item(report, "kuaimai_external.thinktank.cookie")
    assert report.can_migrate is False
    assert item.status == "incomplete"
    assert item.reason == "LEGACY_EXTERNAL_SOURCE_INCOMPLETE"


def test_absent_integrations_are_skipped_not_failed() -> None:
    report = build_legacy_preflight(
        configured_keys=set(),
        organization_corp_id_configured=False,
        corp_id_sources_match=None,
    )

    assert report.can_migrate is True
    assert all(item.status == "skipped" for item in report.items)
