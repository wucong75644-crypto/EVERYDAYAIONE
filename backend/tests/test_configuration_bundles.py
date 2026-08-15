"""Fixed Bundle RPC and Secret material tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.configuration.bundles import (
    SecretBundleResolver,
    WecomBotTargetResolver,
)
from core.db_scope import DatabaseAccessKind
from services.configuration.envelope import (
    KekVersionMissingError,
    SecretMaterialError,
)
from services.configuration.resolver import ConfigurationResolutionError


ORG_ID = "10000000-0000-0000-0000-000000000001"


class FakeDB:
    def __init__(self, data: object) -> None:
        self.data = data
        self.calls: list[tuple[str, object]] = []

    def rpc(self, name: str, params: object = None) -> SimpleNamespace:
        self.calls.append((name, params))
        return SimpleNamespace(
            execute=lambda: SimpleNamespace(data=self.data)
        )


def _erp_response() -> dict[str, object]:
    def secret(key: str) -> dict[str, object]:
        return {
            "key": key,
            "required": True,
            "configured": True,
            "source": "organization",
            "scope_id": ORG_ID,
            "version": 1,
            "value_kind": "secret",
            "secret_ref": {
                "secret_name": key,
                "payload_ciphertext": f"cipher-{key}",
                "wrapped_dek": f"wrapped-{key}",
                "kek_version": "local-v1",
                "payload_version": 1,
            },
        }

    return {
        "bundle": "erp.runtime",
        "definition_version": "v1",
        "items": [
            secret("erp.app_credentials"),
            secret("erp.token_pair"),
            {
                "key": "erp.warehouse_ids",
                "required": False,
                "configured": True,
                "source": "organization",
                "scope_id": ORG_ID,
                "version": 2,
                "value_kind": "json",
                "value_json": ["w1", "w2"],
            },
        ],
    }


def _wecom_response(org_id: str = ORG_ID) -> dict[str, object]:
    return {
        "bundle": "wecom.bot",
        "definition_version": "v1",
        "items": [
            {
                "key": "wecom.corp_id",
                "required": True,
                "configured": True,
                "source": "organization",
                "scope_id": org_id,
                "version": 1,
                "value_kind": "string",
                "value_json": "corp-1",
            },
            {
                "key": "wecom.bot_credentials",
                "required": True,
                "configured": True,
                "source": "organization",
                "scope_id": org_id,
                "version": 2,
                "value_kind": "secret",
                "secret_ref": {
                    "secret_name": "wecom.bot_credentials",
                    "payload_ciphertext": "cipher",
                    "wrapped_dek": "wrapped",
                    "kek_version": "local-v1",
                    "payload_version": 2,
                },
            },
        ],
    }


def test_erp_bundle_decrypts_with_database_selected_scope_and_version() -> None:
    db = FakeDB(_erp_response())
    material = MagicMock()
    material.decrypt_payload.side_effect = (
        {"app_key": "app", "app_secret": "secret"},
        {"access_token": "access", "refresh_token": "refresh"},
    )

    result = SecretBundleResolver(db, material).erp_runtime()

    assert db.calls == [("get_erp_runtime_bundle", None)]
    assert result.values["erp.app_credentials"] == {
        "app_key": "app",
        "app_secret": "secret",
    }
    assert result.values["erp.warehouse_ids"] == ["w1", "w2"]
    assert result.sources["erp.token_pair"] == "organization"
    assert result.versions["erp.warehouse_ids"] == 2
    first_call = material.decrypt_payload.call_args_list[0]
    assert first_call.kwargs["scope_kind"] == "organization"
    assert first_call.kwargs["scope_id"] == ORG_ID
    assert first_call.kwargs["secret_name"] == "erp.app_credentials"
    assert first_call.args[0].payload_version == 1


def test_decrypted_payload_schema_mismatch_fails_closed() -> None:
    material = MagicMock()
    material.decrypt_payload.side_effect = (
        {"app_key": "app", "unexpected": "secret"},
    )

    with pytest.raises(
        ConfigurationResolutionError,
        match="SECRET_MATERIAL_UNAVAILABLE",
    ):
        SecretBundleResolver(FakeDB(_erp_response()), material).erp_runtime()


def test_missing_kek_version_keeps_stable_error_code() -> None:
    material = MagicMock()
    material.decrypt_payload.side_effect = KekVersionMissingError(
        "KEK_VERSION_MISSING"
    )

    with pytest.raises(
        ConfigurationResolutionError,
        match="KEK_VERSION_MISSING",
    ):
        SecretBundleResolver(FakeDB(_erp_response()), material).erp_runtime()


def test_secret_material_failure_keeps_stable_unavailable_code() -> None:
    material = MagicMock()
    material.decrypt_payload.side_effect = SecretMaterialError(
        "cipher details must not leak"
    )

    with pytest.raises(
        ConfigurationResolutionError,
        match="^SECRET_MATERIAL_UNAVAILABLE$",
    ):
        SecretBundleResolver(FakeDB(_erp_response()), material).erp_runtime()


def test_optional_missing_is_preserved_as_none() -> None:
    data = _erp_response()
    data["items"][2] = {
        "key": "erp.warehouse_ids",
        "required": False,
        "configured": False,
    }
    material = MagicMock()
    material.decrypt_payload.side_effect = (
        {"app_key": "app", "app_secret": "secret"},
        {"access_token": "access", "refresh_token": "refresh"},
    )

    result = SecretBundleResolver(FakeDB(data), material).erp_runtime()

    assert result.values["erp.warehouse_ids"] is None
    assert result.sources["erp.warehouse_ids"] is None
    assert result.versions["erp.warehouse_ids"] == 0


def test_database_error_is_mapped_without_leaking_message() -> None:
    class FailingDB:
        def rpc(self, _name: str):
            raise RuntimeError(
                "CONFIG_BUNDLE_AUTHORITY_DENIED token=must-not-leak"
            )

    with pytest.raises(ConfigurationResolutionError) as captured:
        SecretBundleResolver(FailingDB(), MagicMock()).ai_google()

    assert str(captured.value) == "CONFIG_BUNDLE_AUTHORITY_DENIED"
    assert "must-not-leak" not in str(captured.value)


def test_unknown_database_error_uses_generic_secret_free_code() -> None:
    class FailingDB:
        def rpc(self, _name: str):
            raise RuntimeError("driver password=must-not-leak")

    with pytest.raises(ConfigurationResolutionError) as captured:
        SecretBundleResolver(FailingDB(), MagicMock()).ai_google()

    assert str(captured.value) == "CONFIG_BUNDLE_UNAVAILABLE"
    assert "must-not-leak" not in str(captured.value)


@pytest.mark.parametrize(
    ("method", "rpc_name"),
    (
        ("ai_dashscope", "get_ai_dashscope_bundle"),
        ("ai_openrouter", "get_ai_openrouter_bundle"),
        ("ai_kie", "get_ai_kie_bundle"),
        ("ai_google", "get_ai_google_bundle"),
        ("erp_runtime", "get_erp_runtime_bundle"),
        ("wecom_bot", "get_wecom_bot_bundle"),
        ("wecom_bot_admin_test", "get_wecom_bot_admin_test_bundle"),
        ("wecom_oauth_public", "get_wecom_oauth_public_bundle"),
        ("wecom_oauth_exchange", "get_wecom_oauth_exchange_bundle"),
        ("wecom_contact", "get_wecom_contact_bundle"),
        ("wecom_callback", "get_wecom_callback_bundle"),
        ("kuaimai_thinktank", "get_kuaimai_thinktank_bundle"),
        ("kuaimai_viperp", "get_kuaimai_viperp_bundle"),
    ),
)
def test_public_methods_call_only_their_fixed_rpc(
    method: str,
    rpc_name: str,
) -> None:
    effective = MagicMock()
    effective.parse.side_effect = ConfigurationResolutionError("stop")
    db = FakeDB({})

    with pytest.raises(ConfigurationResolutionError, match="stop"):
        getattr(SecretBundleResolver(db, MagicMock(), effective), method)()

    assert db.calls == [(rpc_name, None)]


def test_wecom_targets_use_discovery_then_exact_org_bundle() -> None:
    discovery_db = FakeDB([
        {"org_id": ORG_ID, "credential_version": 2},
    ])
    execution_db = FakeDB(_wecom_response())
    material = MagicMock()
    material.decrypt_payload.return_value = {
        "bot_id": "bot-1",
        "bot_secret": "secret-1",
    }

    class Resolver(WecomBotTargetResolver):
        def __init__(self) -> None:
            super().__init__(MagicMock(), material)
            self.scopes: list[tuple[str | None, str]] = []

        def _scoped_worker_db(self, *, org_id, request_id):
            self.scopes.append((org_id, request_id))
            return discovery_db if org_id is None else execution_db

    resolver = Resolver()
    targets = resolver.list_targets()

    assert resolver.scopes == [
        (None, "wecom-bot-discovery"),
        (ORG_ID, f"wecom-bot:{ORG_ID}"),
    ]
    assert discovery_db.calls == [("discover_wecom_bot_targets", None)]
    assert execution_db.calls == [("get_wecom_bot_bundle", None)]
    assert targets[0].org_id == ORG_ID
    assert targets[0].corp_id == "corp-1"
    assert targets[0].bot_id == "bot-1"
    assert targets[0].bot_secret == "secret-1"


def test_wecom_target_failure_isolated_to_affected_org() -> None:
    second_org = "20000000-0000-0000-0000-000000000002"
    discovery_db = FakeDB([
        {"org_id": "invalid", "credential_version": 1},
        {"org_id": second_org, "credential_version": 1},
    ])
    execution_db = FakeDB(_wecom_response(second_org))
    material = MagicMock()
    material.decrypt_payload.return_value = {
        "bot_id": "bot-2",
        "bot_secret": "secret-2",
    }

    class Resolver(WecomBotTargetResolver):
        def _scoped_worker_db(self, *, org_id, request_id):
            if org_id == "invalid":
                raise ValueError("invalid org")
            return discovery_db if org_id is None else execution_db

    targets = Resolver(MagicMock(), material).list_targets()

    assert [target.org_id for target in targets] == [second_org]


def test_wecom_target_scopes_are_actorless_and_exact() -> None:
    resolver = WecomBotTargetResolver(MagicMock(), MagicMock())

    discovery = resolver._scoped_worker_db(
        org_id=None,
        request_id="wecom-bot-discovery",
    )
    execution = resolver._scoped_worker_db(
        org_id=ORG_ID,
        request_id=f"wecom-bot:{ORG_ID}",
    )

    assert discovery.scope.actor_user_id is None
    assert discovery.scope.org_id is None
    assert discovery.scope.access_kind is DatabaseAccessKind.WORKER
    assert execution.scope.actor_user_id is None
    assert execution.scope.org_id == ORG_ID
    assert execution.scope.access_kind is DatabaseAccessKind.WORKER


def test_wecom_target_rejects_malformed_decrypted_bundle() -> None:
    execution_db = FakeDB(_wecom_response())
    material = MagicMock()
    material.decrypt_payload.return_value = {
        "bot_id": "bot-without-secret",
    }

    class Resolver(WecomBotTargetResolver):
        def _scoped_worker_db(self, *, org_id, request_id):
            return execution_db

    with pytest.raises(
        ConfigurationResolutionError,
        match="SECRET_MATERIAL_UNAVAILABLE",
    ):
        Resolver(MagicMock(), material)._resolve_target(ORG_ID)
