"""Legacy configuration import transformation tests."""

from __future__ import annotations

import base64
from typing import Literal

import pytest

from services.configuration.envelope import LocalKEKProvider
from services.configuration.legacy_import import (
    LegacyExternalValue,
    LegacyImportPlanError,
    LegacyImportPlanner,
    LegacyOrganizationValues,
)
from services.configuration.legacy_migration import build_legacy_preflight
from services.configuration.legacy_migration import ExternalCredentialFact
from services.configuration.material_service import SecretMaterialService


ORG_ID = "00000000-0000-0000-0000-000000000010"
IMPORT_ID = "00000000-0000-0000-0000-00000000a161"
SECRET_MARKERS = {
    "ai_google_api_key": "GOOGLE_SECRET",
    "kuaimai_app_key": "APP_KEY_SECRET",
    "kuaimai_app_secret": "APP_SECRET",
    "kuaimai_access_token": "ACCESS_SECRET",
    "kuaimai_refresh_token": "REFRESH_SECRET",
    "erp_warehouse_ids": " 1,2,1, ,3 ",
    "wecom_bot_id": "BOT_ID_SECRET",
    "wecom_bot_secret": "BOT_SECRET",
    "wecom_agent_id": "agent-1",
    "wecom_agent_secret": "AGENT_SECRET",
    "wecom_corp_id": "corp-1",
}


def _planner() -> LegacyImportPlanner:
    provider = LocalKEKProvider(
        current_version="test-v1",
        keyring={"test-v1": b"k" * 32},
    )
    return LegacyImportPlanner(SecretMaterialService(provider))


def _source(
    *,
    external_status: Literal["active", "expired", "invalid"] = "active",
    cookie_full: str = "FULL_SECRET",
) -> LegacyOrganizationValues:
    return LegacyOrganizationValues(
        org_id=ORG_ID,
        organization_corp_id="corp-1",
        config_values=SECRET_MARKERS,
        external_credentials=(
            LegacyExternalValue(
                source="thinktank",
                status=external_status,
                company_id="123",
                censeid_cookie="CENSEID_SECRET",
                cookie_full=cookie_full,
            ),
        ),
    )


def _report(
    *,
    external_status: Literal["active", "expired", "invalid"] = "active",
):
    return build_legacy_preflight(
        configured_keys=set(SECRET_MARKERS),
        organization_corp_id_configured=True,
        corp_id_sources_match=True,
        external_credentials=(
            ExternalCredentialFact(
                "thinktank",
                external_status,
                True,
                True,
            ),
        ),
    )


def test_planner_transforms_values_and_never_embeds_plaintext_secrets() -> None:
    plan = _planner().build(
        import_id=IMPORT_ID,
        organizations=(_source(),),
        preflight_reports={ORG_ID: _report()},
    )

    by_key = {item.config_key: item for item in plan.items}
    assert plan.org_count == 1
    assert plan.item_count == 10
    assert "erp.app_credentials" in plan.config_keys
    assert by_key["erp.warehouse_ids"].value_json == ["1", "2", "3"]
    assert by_key["wecom.corp_id"].value_json == "corp-1"
    assert by_key[
        "kuaimai_external.thinktank.company_id"
    ].value_json == "123"
    assert all(
        item.secret_envelope is None or item.value_json is None
        for item in plan.items
    )
    serialized = str([
        item.database_value() for item in plan.items
    ])
    for marker in (
        "GOOGLE_SECRET",
        "APP_SECRET",
        "ACCESS_SECRET",
        "CENSEID_SECRET",
        "FULL_SECRET",
    ):
        assert marker not in serialized
    assert base64.b64decode(
        by_key["erp.app_credentials"].secret_envelope[
            "payload_ciphertext"
        ],
        validate=True,
    )


def test_expired_external_credential_stays_unconfigured() -> None:
    plan = _planner().build(
        import_id=IMPORT_ID,
        organizations=(_source(external_status="expired"),),
        preflight_reports={ORG_ID: _report(external_status="expired")},
    )

    assert not any(
        item.config_key.startswith("kuaimai_external.")
        for item in plan.items
    )


def test_blocked_preflight_prevents_any_plan() -> None:
    report = build_legacy_preflight(
        configured_keys={"kuaimai_app_key"},
        organization_corp_id_configured=False,
        corp_id_sources_match=None,
    )

    with pytest.raises(
        LegacyImportPlanError,
        match="LEGACY_IMPORT_PREFLIGHT_BLOCKED",
    ):
        _planner().build(
            import_id=IMPORT_ID,
            organizations=(_source(),),
            preflight_reports={ORG_ID: report},
        )


def test_missing_external_cookie_value_fails_closed() -> None:
    with pytest.raises(
        LegacyImportPlanError,
        match="LEGACY_IMPORT_VALUE_INVALID",
    ):
        _planner().build(
            import_id=IMPORT_ID,
            organizations=(_source(cookie_full=""),),
            preflight_reports={ORG_ID: _report()},
        )


def test_plan_repr_does_not_expose_values_or_envelopes() -> None:
    plan = _planner().build(
        import_id=IMPORT_ID,
        organizations=(_source(),),
        preflight_reports={ORG_ID: _report()},
    )

    rendered = repr(plan)
    assert "SECRET" not in rendered
    assert "payload_ciphertext" not in rendered


def test_organization_sets_must_match_preflight_exactly() -> None:
    with pytest.raises(
        LegacyImportPlanError,
        match="LEGACY_IMPORT_ORGANIZATION_MISMATCH",
    ):
        _planner().build(
            import_id=IMPORT_ID,
            organizations=(_source(), _source()),
            preflight_reports={ORG_ID: _report()},
        )


def test_empty_legacy_sources_do_not_create_an_import() -> None:
    report = build_legacy_preflight(
        configured_keys=set(),
        organization_corp_id_configured=False,
        corp_id_sources_match=None,
    )

    with pytest.raises(LegacyImportPlanError, match="LEGACY_IMPORT_EMPTY"):
        _planner().build(
            import_id=IMPORT_ID,
            organizations=(
                LegacyOrganizationValues(ORG_ID, None, {}),
            ),
            preflight_reports={ORG_ID: report},
        )


def test_duplicate_external_source_fails_closed() -> None:
    source = _source()
    duplicate = LegacyOrganizationValues(
        org_id=source.org_id,
        organization_corp_id=source.organization_corp_id,
        config_values=source.config_values,
        external_credentials=source.external_credentials * 2,
    )

    with pytest.raises(
        LegacyImportPlanError,
        match="LEGACY_IMPORT_EXTERNAL_DUPLICATE",
    ):
        _planner().build(
            import_id=IMPORT_ID,
            organizations=(duplicate,),
            preflight_reports={ORG_ID: _report()},
        )


def test_active_external_requires_matching_ready_targets() -> None:
    with pytest.raises(
        LegacyImportPlanError,
        match="LEGACY_IMPORT_PREFLIGHT_MISMATCH",
    ):
        _planner().build(
            import_id=IMPORT_ID,
            organizations=(_source(),),
            preflight_reports={
                ORG_ID: build_legacy_preflight(
                    configured_keys=set(SECRET_MARKERS),
                    organization_corp_id_configured=True,
                    corp_id_sources_match=True,
                ),
            },
        )


def test_missing_ready_source_and_empty_warehouse_fail_closed() -> None:
    values = dict(SECRET_MARKERS)
    values.pop("kuaimai_app_secret")
    with pytest.raises(
        LegacyImportPlanError,
        match="LEGACY_IMPORT_SOURCE_MISSING",
    ):
        _planner().build(
            import_id=IMPORT_ID,
            organizations=(
                LegacyOrganizationValues(ORG_ID, "corp-1", values),
            ),
            preflight_reports={ORG_ID: _report(external_status="expired")},
        )

    values = dict(SECRET_MARKERS)
    values["erp_warehouse_ids"] = " , "
    with pytest.raises(
        LegacyImportPlanError,
        match="LEGACY_IMPORT_VALUE_INVALID",
    ):
        _planner().build(
            import_id=IMPORT_ID,
            organizations=(
                LegacyOrganizationValues(ORG_ID, "corp-1", values),
            ),
            preflight_reports={ORG_ID: _report(external_status="expired")},
        )


@pytest.mark.parametrize(
    "import_id",
    ["not-a-uuid", "00000000-0000-0000-0000-00000000A161"],
)
def test_import_id_requires_canonical_uuid(import_id: str) -> None:
    with pytest.raises(
        LegacyImportPlanError,
        match="LEGACY_IMPORT_ID_INVALID",
    ):
        _planner().build(
            import_id=import_id,
            organizations=(_source(),),
            preflight_reports={ORG_ID: _report()},
        )
