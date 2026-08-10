from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.agent.runtime.credential_broker import CredentialLease
from services.agent.runtime.domain import RuntimeScope, ScopeKind
from services.agent.runtime.wecom_app_credentials import (
    WECOM_APP_PROVIDER,
    WECOM_APP_SEND_PURPOSE,
    build_wecom_app_token_provider,
)


NOW = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
HANDLE = "credential:org-a:wecom-app"
REVISION = "wecom-app-v1"
RAW_SECRET = "raw-secret-test-only"
TOKEN = "access-token-test-only"


def _scope(org_id: str = "org-a") -> RuntimeScope:
    return RuntimeScope(ScopeKind.USER, "user-a", "user-a", org_id)


class _Exchange:
    operational = True
    production_ready = True

    def __init__(self, *, result: object = TOKEN, failure: BaseException | None = None) -> None:
        self.result = result
        self.failure = failure
        self.material_ids: list[int] = []

    async def exchange(self, material: object) -> object:
        self.material_ids.append(id(material))
        if self.failure is not None:
            raise self.failure
        assert material == {"opaque": RAW_SECRET}
        return self.result

    def __repr__(self) -> str:
        return "_Exchange(secret-free)"


class _Broker:
    def __init__(self, lease: CredentialLease[object]) -> None:
        self.lease = lease
        self.calls: list[dict[str, object]] = []

    def require_production_ready(self) -> None:
        pass

    async def resolve(self, **binding: object) -> CredentialLease[object]:
        self.calls.append(binding)
        return self.lease


def _lease(
    *,
    handle: str = HANDLE,
    scope: RuntimeScope | None = None,
    provider: str = WECOM_APP_PROVIDER,
    revision: str = REVISION,
    purpose: str = WECOM_APP_SEND_PURPOSE,
    expires_at: datetime | None = None,
) -> CredentialLease[object]:
    binding_scope = scope or _scope()
    return CredentialLease(
        tenant_id=binding_scope.org_id or binding_scope.user_id,
        handle=handle,
        provider=provider,
        revision=revision,
        purpose=purpose,
        expires_at=expires_at or NOW + timedelta(minutes=1),
        material={"opaque": RAW_SECRET},
        clock=lambda: NOW,
    )


def _provider(
    broker: object,
    exchange: object,
    *,
    scope: RuntimeScope | None = None,
    revision: str = REVISION,
):
    return build_wecom_app_token_provider(
        broker=broker,  # type: ignore[arg-type]
        scope=scope or _scope(),
        credential_handle=HANDLE,
        provider_revision=revision,
        token_exchange=exchange,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_each_call_resolves_then_consumes_the_same_binding() -> None:
    lease = _lease()
    broker = _Broker(lease)
    exchange = _Exchange()
    provider = _provider(broker, exchange)

    assert await provider() == TOKEN
    assert await provider() == TOKEN
    expected = {
        "scope": _scope(),
        "credential_handle": HANDLE,
        "provider": WECOM_APP_PROVIDER,
        "revision": REVISION,
        "purpose": WECOM_APP_SEND_PURPOSE,
    }
    assert broker.calls == [expected, expected]
    assert exchange.material_ids == [id(lease._material), id(lease._material)]
    evidence = repr(provider) + repr(lease) + repr(exchange)
    assert RAW_SECRET not in evidence
    assert TOKEN not in evidence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lease_scope", "provider", "revision", "purpose"),
    [
        (_scope("org-b"), WECOM_APP_PROVIDER, REVISION, WECOM_APP_SEND_PURPOSE),
        (_scope(), "other", REVISION, WECOM_APP_SEND_PURPOSE),
        (_scope(), WECOM_APP_PROVIDER, "other", WECOM_APP_SEND_PURPOSE),
        (_scope(), WECOM_APP_PROVIDER, REVISION, "other"),
    ],
)
async def test_lease_binding_mismatch_fails_closed(
    lease_scope: RuntimeScope,
    provider: str,
    revision: str,
    purpose: str,
) -> None:
    exchange = _Exchange()
    token_provider = _provider(
        _Broker(_lease(
            scope=lease_scope,
            provider=provider,
            revision=revision,
            purpose=purpose,
        )),
        exchange,
    )

    assert await token_provider() is None
    assert exchange.material_ids == []


@pytest.mark.asyncio
async def test_expired_lease_fails_before_exchange() -> None:
    exchange = _Exchange()
    provider = _provider(_Broker(_lease(expires_at=NOW)), exchange)

    assert await provider() is None
    assert exchange.material_ids == []


@pytest.mark.asyncio
async def test_resolved_lease_handle_must_match_captured_handle() -> None:
    exchange = _Exchange()
    provider = _provider(_Broker(_lease(handle="different-opaque-handle")), exchange)

    assert await provider() is None
    assert exchange.material_ids == []


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, "", " ", " padded", 7])
async def test_exchange_non_token_results_fail_closed(result: object) -> None:
    assert await _provider(_Broker(_lease()), _Exchange(result=result))() is None


@pytest.mark.asyncio
async def test_broker_exchange_and_material_failures_are_redacted_to_none() -> None:
    class FailingBroker:
        def require_production_ready(self) -> None:
            pass

        async def resolve(self, **binding: object) -> CredentialLease[object]:
            raise RuntimeError(RAW_SECRET)

    class FailingMaterial:
        def __eq__(self, other: object) -> bool:
            raise RuntimeError(RAW_SECRET)

        def __repr__(self) -> str:
            raise RuntimeError(RAW_SECRET)

    material_lease = CredentialLease(
        tenant_id="org-a",
        handle=HANDLE,
        provider=WECOM_APP_PROVIDER,
        revision=REVISION,
        purpose=WECOM_APP_SEND_PURPOSE,
        expires_at=NOW + timedelta(minutes=1),
        material=FailingMaterial(),
        clock=lambda: NOW,
    )
    broker_result = await _provider(FailingBroker(), _Exchange())()
    exchange_result = await _provider(
        _Broker(_lease()), _Exchange(failure=RuntimeError(RAW_SECRET)),
    )()
    material_result = await _provider(_Broker(material_lease), _Exchange())()

    assert broker_result is exchange_result is material_result is None
    assert RAW_SECRET not in repr((broker_result, exchange_result, material_result))


@pytest.mark.asyncio
async def test_exchange_cancellation_propagates() -> None:
    entered = asyncio.Event()

    class WaitingExchange(_Exchange):
        async def exchange(self, material: object) -> str:
            entered.set()
            await asyncio.Event().wait()
            return TOKEN

    lease = _lease()
    material_id = id(lease._material)
    task = asyncio.create_task(_provider(_Broker(lease), WaitingExchange())())
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    error = caught.value
    assert error.__context__ is None
    assert error.__cause__ is None
    traceback = error.__traceback__
    while traceback is not None:
        for value in traceback.tb_frame.f_locals.values():
            assert id(value) != material_id
            try:
                rendered = repr(value)
            except Exception:
                rendered = ""
            assert RAW_SECRET not in rendered
        traceback = traceback.tb_next


@pytest.mark.asyncio
async def test_broker_resolve_cancellation_propagates_directly() -> None:
    class CancellingBroker:
        def require_production_ready(self) -> None:
            pass

        async def resolve(self, **binding: object) -> CredentialLease[object]:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _provider(CancellingBroker(), _Exchange())()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exchange",
    [
        type("MissingReadiness", (), {"exchange": _Exchange.exchange})(),
        type("NotOperational", (_Exchange,), {"operational": False})(),
        type("MockExchange", (_Exchange,), {"production_ready": False})(),
    ],
)
async def test_missing_or_false_exchange_readiness_fails_closed(exchange: object) -> None:
    broker = _Broker(_lease())

    assert await _provider(broker, exchange)() is None
    assert broker.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("readiness", ["missing", "false", "error"])
async def test_broker_production_readiness_is_required_before_resolve(
    readiness: str,
) -> None:
    lease = _lease()

    class Broker:
        calls = 0

        if readiness == "false":
            def require_production_ready(self) -> bool:
                return False
        elif readiness == "error":
            def require_production_ready(self) -> None:
                raise RuntimeError(RAW_SECRET)

        async def resolve(self, **binding: object) -> CredentialLease[object]:
            self.calls += 1
            return lease

    broker = Broker()
    assert await _provider(broker, _Exchange())() is None
    assert broker.calls == 0


def test_module_has_no_forbidden_credential_dependencies() -> None:
    source = (
        Path(__file__).parents[1]
        / "services/agent/runtime/wecom_app_credentials.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "settings",
        "get_access_token",
        "Redis",
        "AsyncOrgConfigResolver",
    )
    assert not any(name in source for name in forbidden)
