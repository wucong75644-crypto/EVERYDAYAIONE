from types import SimpleNamespace

import pytest

from services.agent.runtime.nonproduction_composition import (
    build_local_nonproduction_runtime_profile,
)


def test_local_profile_is_explicit_but_never_production_ready(tmp_path) -> None:
    profile = build_local_nonproduction_runtime_profile(
        root=tmp_path / "root", staging=tmp_path / "staging",
    )
    assembly = profile.assembly
    assert assembly.readiness.service_wiring_ready is True
    assert assembly.readiness.credential_available is False
    assert assembly.readiness.production_ready is False
    assert assembly.readiness.ready is False
    assert profile.credential_broker.readiness().production_ready is False
    assert profile.provider_facts.production_ready is False
    assert profile.object_store.production_ready is False
    assert profile.assembly.scheduler.readiness.production_ready is False


@pytest.mark.asyncio
async def test_local_profile_resolves_only_explicit_mock_provider(tmp_path) -> None:
    profile = build_local_nonproduction_runtime_profile(
        root=tmp_path / "root", staging=tmp_path / "staging",
    )
    scope = SimpleNamespace(org_id="nonprod-tenant", user_id="user-a")
    binding = await profile.provider_resolver.resolve(scope, "erp")
    assert binding.ready is True
    assert binding.provider.readiness.production_ready is False
    with pytest.raises(RuntimeError, match="BINDING_NOT_FOUND"):
        await profile.provider_resolver.resolve(scope, "scheduler")


def test_local_profile_contains_no_legacy_or_global_object_store_entrypoint() -> None:
    from pathlib import Path
    source = Path(__file__).parents[1] / "services/agent/runtime/nonproduction_composition.py"
    text = source.read_text()
    for forbidden in ("get_oss_service", "ErpDispatcher", "KieMediaProvider", "RedisClient"):
        assert forbidden not in text
