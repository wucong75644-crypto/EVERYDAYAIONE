from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
import pickle
from types import MappingProxyType

import pytest

from services.configuration.bundles import ResolvedConfigurationBundle
from services.configuration.material_service import SecretMaterialService
from services.wecom import scheduled_app_binding as adapter


ORG_ID = "10000000-0000-0000-0000-000000000001"
CORP_ID = "corp-a"
SECRET = "scheduled-secret-test-only"
TOKEN = "scheduled-token-test-only"
KEYS = (
    "wecom.corp_id",
    "wecom.oauth_agent_id",
    "wecom.oauth_agent_secret",
)


def _bundle(*, secret: str = SECRET, secret_version: int = 3) -> ResolvedConfigurationBundle:
    return ResolvedConfigurationBundle(
        name="wecom.app",
        values=MappingProxyType({
            "wecom.corp_id": CORP_ID,
            "wecom.oauth_agent_id": "1001",
            "wecom.oauth_agent_secret": {"agent_secret": secret},
        }),
        sources=MappingProxyType({key: "organization" for key in KEYS}),
        versions=MappingProxyType(dict(zip(
            KEYS, (1, 2, secret_version), strict=True,
        ))),
    )


class _Database:
    def rpc(self, _name: str, _params: object = None) -> object:
        raise AssertionError("patched resolver owns reads")


class _HttpClient:
    async def post(self, _url: str, **_kwargs: object) -> object:
        raise AssertionError("token-only tests must not send HTTP")


class _AuditSink:
    async def record(self, _event: object) -> None:
        return None


class _TokenManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    async def __call__(
        self,
        org_id: str,
        corp_id: str,
        secret: str,
        *,
        credential_revision: str,
    ) -> str:
        self.calls.append((org_id, corp_id, secret, credential_revision))
        return TOKEN


def _resolver(
    monkeypatch: pytest.MonkeyPatch,
    bundles: dict[str, ResolvedConfigurationBundle],
    token_manager: _TokenManager,
) -> adapter.ScheduledWecomAppBindingResolver:
    class BundleResolver:
        def __init__(self, database: object, _material: object) -> None:
            self._scope = database.scope  # type: ignore[attr-defined]

        async def wecom_app(self) -> ResolvedConfigurationBundle:
            return bundles[self._scope.org_id]

    monkeypatch.setattr(adapter, "AsyncSecretBundleResolver", BundleResolver)
    return adapter.ScheduledWecomAppBindingResolver(
        database=_Database(),
        material_service=SecretMaterialService(object()),  # type: ignore[arg-type]
        get_access_token=token_manager,
        outbound_http_client=_HttpClient(),
        audit_sink=_AuditSink(),
    )


def test_secret_material_rejects_generic_serialization_boundaries() -> None:
    material = adapter._WecomAppMaterial(ORG_ID, CORP_ID, SECRET)

    assert repr(material) == "_WecomAppMaterial(<redacted>)"
    with pytest.raises(TypeError):
        dataclasses.asdict(material)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        vars(material)
    with pytest.raises(TypeError, match="WECOM_APP_MATERIAL_NOT_SERIALIZABLE"):
        material.__getstate__()
    with pytest.raises(TypeError, match="WECOM_APP_MATERIAL_NOT_SERIALIZABLE"):
        pickle.dumps(material)
    assert SECRET not in repr(material)


@pytest.mark.asyncio
async def test_config_version_rotation_passes_a_new_opaque_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundles = {ORG_ID: _bundle()}
    token_manager = _TokenManager()
    resolver = _resolver(monkeypatch, bundles, token_manager)
    old_binding = await resolver.resolve_app_binding(org_id=ORG_ID, corp_id=CORP_ID)
    bundles[ORG_ID] = _bundle(secret="rotated-secret", secret_version=4)
    new_binding = await resolver.resolve_app_binding(org_id=ORG_ID, corp_id=CORP_ID)

    assert old_binding is not None and new_binding is not None
    assert await old_binding.transport._token_provider() == TOKEN  # type: ignore[attr-defined]
    assert await new_binding.transport._token_provider() == TOKEN  # type: ignore[attr-defined]
    old_call, new_call = token_manager.calls
    assert old_call[3] != new_call[3]
    assert (old_call[2], new_call[2]) == (SECRET, "rotated-secret")
    assert CORP_ID not in new_call[3] and "rotated-secret" not in new_call[3]


@pytest.mark.asyncio
async def test_expired_old_binding_cannot_issue_a_new_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_manager = _TokenManager()
    monkeypatch.setattr(
        adapter,
        "_utc_now",
        lambda: datetime.now(timezone.utc) - timedelta(minutes=6),
    )
    binding = await _resolver(
        monkeypatch, {ORG_ID: _bundle()}, token_manager,
    ).resolve_app_binding(org_id=ORG_ID, corp_id=CORP_ID)

    assert binding is not None
    assert await binding.transport._token_provider() is None  # type: ignore[attr-defined]
    assert token_manager.calls == []
